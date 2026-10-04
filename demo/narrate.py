"""Small printing helpers so the three demo scripts narrate consistently."""


def header(title: str):
    print()
    print("=" * 70)
    print(title)
    print("=" * 70)


def sub(title: str):
    print()
    print(f"--- {title} ---")


def line(text: str = ""):
    print(text)


def result(label: str, ok: bool, detail: str = ""):
    mark = "BAŞARILI " if ok else "BAŞARISIZ"
    print(f"  [{mark}] {label}" + (f" -- {detail}" if detail else ""))
