"""Portable keyword Boolean grammar shared by every interface and provider.

NOT binds before AND, which binds before OR. Adjacent terms imply AND;
double-quoted phrases are indivisible literals. All positions are zero-based.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
import unicodedata
from typing import Callable


class SearchExpressionError(ValueError):
    def __init__(self, message: str, position: int = 0) -> None:
        super().__init__(message)
        self.position = position


@dataclass(frozen=True, slots=True)
class Term:
    text: str
    quoted: bool = False


@dataclass(frozen=True, slots=True)
class Not:
    operand: Expression


@dataclass(frozen=True, slots=True)
class Binary:
    operator: str
    left: Expression
    right: Expression


Expression = Term | Not | Binary


@dataclass(frozen=True, slots=True)
class _Token:
    kind: str
    text: str
    position: int


def _tokens(expression: str) -> list[_Token]:
    if len(expression) > 5000:
        raise SearchExpressionError("Search expressions must be at most 5,000 characters.")
    tokens = []
    index = 0
    while index < len(expression):
        char = expression[index]
        if char.isspace():
            index += 1
            continue
        start = index
        if char in "()":
            tokens.append(_Token(char, char, index))
            index += 1
        elif char == '"':
            index += 1
            end = expression.find('"', index)
            if end == -1:
                raise SearchExpressionError("Close the quoted phrase with a double quote.", start)
            text = expression[index:end]
            if not text.strip():
                raise SearchExpressionError("Quoted phrases cannot be empty.", start)
            if "\\" in text or any(ord(c) < 32 for c in text):
                raise SearchExpressionError("Use plain text inside quoted phrases.", start)
            tokens.append(_Token("PHRASE", " ".join(text.split()), start))
            index = end + 1
        else:
            while index < len(expression) and not expression[index].isspace() and expression[index] not in '()"':
                if expression[index] in "[]{}:*?~^\\|&+=!;" or ord(expression[index]) < 32:
                    raise SearchExpressionError(
                        "Use keywords, quoted phrases, AND, OR, NOT and parentheses; source field tags and wildcards are not portable.",
                        index,
                    )
                index += 1
            text = expression[start:index]
            kind = text.upper() if text.upper() in {"AND", "OR", "NOT"} else "WORD"
            tokens.append(_Token(kind, text, start))
        if len(tokens) > 256:
            raise SearchExpressionError("Use at most 256 search tokens.", start)
    tokens.append(_Token("END", "", len(expression)))
    return tokens


def parse_expression(expression: str) -> Expression:
    tokens = _tokens(expression)
    index = 0

    def primary(depth: int, *, single_word: bool = False) -> Expression:
        nonlocal index
        token = tokens[index]
        if depth > 32:
            raise SearchExpressionError("Use at most 32 levels of grouping or NOT.", token.position)
        if token.kind == "NOT":
            index += 1
            # Negation binds to the next operand before implicit adjacency:
            # NOT alpha beta means (NOT alpha) AND beta, not NOT (alpha AND beta).
            return Not(primary(depth + 1, single_word=True))
        if token.kind == "(":
            index += 1
            result = disjunction(depth + 1)
            if tokens[index].kind != ")":
                raise SearchExpressionError("Close this group with a parenthesis.", token.position)
            index += 1
            return result
        if token.kind == "PHRASE":
            index += 1
            return Term(token.text, quoted=True)
        if token.kind == "WORD":
            words = []
            while tokens[index].kind == "WORD":
                words.append(tokens[index].text)
                index += 1
                if single_word:
                    break
            return Term(" ".join(words))
        raise SearchExpressionError("Expected a keyword, quoted phrase or parenthesized group.", token.position)

    def conjunction(depth: int) -> Expression:
        nonlocal index
        result = primary(depth)
        while tokens[index].kind in {"AND", "NOT", "WORD", "PHRASE", "("}:
            if tokens[index].kind == "AND":
                index += 1
            result = Binary("AND", result, primary(depth))
        return result

    def disjunction(depth: int) -> Expression:
        nonlocal index
        result = conjunction(depth)
        while tokens[index].kind == "OR":
            index += 1
            result = Binary("OR", result, conjunction(depth))
        return result

    root = disjunction(0)
    if tokens[index].kind != "END":
        raise SearchExpressionError("Unexpected closing parenthesis.", tokens[index].position)
    return root


def render_expression(node: Expression, *, provider: str | None = None) -> str:
    """Fully group compound operands; PubMed processes ungrouped logic left-to-right."""
    def operand(value: Expression) -> str:
        text = render_expression(value, provider=provider)
        return f"({text})" if isinstance(value, (Binary, Not)) else text

    if isinstance(node, Term):
        return f'"{node.text}"' if node.quoted else node.text
    if isinstance(node, Not):
        if isinstance(node.operand, Not):
            return render_expression(node.operand.operand, provider=provider)
        if provider in {"pubmed", "europe_pmc"}:
            # Europe PMC's (*:* NOT term) changes the result universe when
            # grouped. Its * match-all form preserves standalone NOT semantics.
            universe = "all[sb]" if provider == "pubmed" else "*"
            return f"({universe} NOT {operand(node.operand)})"
        return f"NOT {operand(node.operand)}"
    if node.operator == "AND" and isinstance(node.right, Not) and provider in {"pubmed", "europe_pmc", "openalex"}:
        return f"{operand(node.left)} NOT {operand(node.right.operand)}"
    return f"{operand(node.left)} {node.operator} {operand(node.right)}"


def evaluate_expression(node: Expression, term_matches: Callable[[Term], bool]) -> bool:
    if isinstance(node, Term):
        return term_matches(node)
    if isinstance(node, Not):
        return not evaluate_expression(node.operand, term_matches)
    if node.operator == "AND":
        return evaluate_expression(node.left, term_matches) and evaluate_expression(node.right, term_matches)
    return evaluate_expression(node.left, term_matches) or evaluate_expression(node.right, term_matches)


def candidate_terms(node: Expression) -> tuple[str, ...]:
    """Positive term union is a safe seed cover, unless a negative-only branch can match."""
    if evaluate_expression(node, lambda term: False):
        return ("",)  # Negation-only branch needs a catalogue scan, with date filters.
    seeds = []

    def visit(value: Expression, negative: bool = False) -> None:
        if isinstance(value, Term):
            if not negative:
                seeds.append(value.text)
        elif isinstance(value, Not):
            visit(value.operand, not negative)
        else:
            visit(value.left, negative)
            visit(value.right, negative)
    visit(node)
    return tuple(dict.fromkeys(seeds)) or ("",)


def matches_metadata(node: Expression, fields: tuple[str, ...]) -> bool:
    """Crossref keyword matching over available title/abstract/subject text only."""
    def words(text: str) -> tuple[str, ...]:
        return tuple(re.findall(r"\w+", unicodedata.normalize("NFKC", text).casefold()))
    token_fields = tuple(words(text) for text in fields)
    available = {word for field in token_fields for word in field}

    def match(term: Term) -> bool:
        wanted = words(term.text)
        if not wanted:
            return False
        if not term.quoted:
            return all(word in available for word in wanted)
        return any(any(field[index:index + len(wanted)] == wanted
                       for index in range(len(field) - len(wanted) + 1))
                   for field in token_fields)
    return evaluate_expression(node, match)
