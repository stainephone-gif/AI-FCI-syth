from app.draft.lint import lint
from app.draft.quotes import QuoteFix, QuoteFixes, apply_fixes
from app.draft.schemas import Claim

GOOD = (
    "<b>Заголовок?</b>\n\n<i>Мы задали вопрос.</i>\n\n"
    + "💜 "
    + "Тезис. " * 60
    + "\n\n"
    + "💜 "
    + "Ещё тезис. " * 60
    + "\n\n"
    + "💜 Что это значит для медийщика: проверяйте.\n\n<i>Читайте источник</i> 🌟"
)


def test_lint_passes_good_post() -> None:
    assert lint(GOOD) == []


def test_lint_catches_style_violations() -> None:
    bad = (
        "Сегодня поговорим о важном!!!\n\n- пункт один\n- пункт два\n\n"
        "✅ Подробнее читайте в нашем разборе и в карточках 📌"
    )
    notes = lint(bad)
    joined = " | ".join(notes)
    assert "короткий пост" in joined
    assert "запрещённое начало" in joined
    assert "список через дефис" in joined
    assert "«наш» про чужую работу" in joined
    assert "обещаны карточки" in joined
    assert "восклицательных знаков: 3" in joined
    assert "маркеры не из набора" in joined and "✅" in joined
    assert "нет блока" in joined
    assert "без эмодзи-маркера" in " | ".join(lint("Что это значит для медийщика: x"))


def test_apply_fixes_by_text_then_by_order() -> None:
    claims = [Claim(text="A", quote="перевод"), Claim(text="B", quote="пересказ")]
    fixes = QuoteFixes(quotes=[QuoteFix(text="A", quote="exact A"), QuoteFix(text="B!", quote="")])
    out = apply_fixes(claims, fixes)
    assert [c.quote for c in out] == ["exact A", "пересказ"]
    fixes2 = QuoteFixes(
        quotes=[QuoteFix(text="a", quote="by order A"), QuoteFix(text="b", quote="by order B")]
    )
    assert [c.quote for c in apply_fixes(claims, fixes2)] == ["by order A", "by order B"]
