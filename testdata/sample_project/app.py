"""Tiny fixture project with an intentional coverage hole."""


def add(left: int, right: int) -> int:
    return left + right


def divide(left: int, right: int) -> float:
    if right == 0:
        raise ZeroDivisionError("cannot divide by zero")
    return left / right


def risky(user_input: str):
    """Intentionally untested helper so coverage reports a real hole."""
    return eval(user_input)  # noqa: S307
