from content_agent.checks import check_card, check_facts_used, check_post

KEYWORDS = ["luxury hotel", "отели", "family office"]

GOOD = """Отель на 40 номеров продали дороже соседнего на 200. Это luxury hotel, и дело не в локации.

@aman купил актив в Доломитах за 120 млн евро.

Я заметила одну деталь в их сделке.

Они платили не за ключи, а за историю места.

120 млн за 40 ключей — 3 млн за номер.

Моя версия такая: концепция и дизайн дают премию, которую покупатель готов оплатить сразу.

Это сработает не всем. Но для редких локаций логика именно такая.

Урок для владельцев: прежде чем звать брокера, посчитайте, сколько стоит ваша история.

А вы видели сделки, где концепция весила больше квадратных метров?

#hospitality #luxuryhotels #hotelinvestment #familyoffice"""


def test_good_post_passes():
    r = check_post(GOOD, hero="Aman", handle="@aman", keywords=KEYWORDS)
    assert r.ok, r.errors


def test_banned_words_and_exclamations():
    bad = GOOD.replace("Я заметила", "Не секрет, что я заметила!!")
    r = check_post(bad, hero="Aman", handle="@aman", keywords=KEYWORDS)
    assert any("стоп-листа" in e for e in r.errors)
    assert any("Восклицательных" in e for e in r.errors)


def test_too_short_and_no_hashtags():
    r = check_post("Коротко про 5 отелей.", hero="Aman", handle="@aman", keywords=KEYWORDS)
    assert any("Длина" in e for e in r.errors)
    assert any("Хэштегов" in e for e in r.errors)


def test_missing_hero_mention_and_links():
    bad = GOOD.replace("@aman", "Они") + "\nhttps://example.com"
    r = check_post(bad, hero="Aman", handle="@aman", keywords=KEYWORDS)
    assert any("не отмечен" in e for e in r.errors)
    assert any("Ссылка" in e for e in r.errors)


def test_card_checks():
    card = {
        "facts": [{"fact": "a", "source_url": ""}, {"fact": "b", "source_url": "https://x"}],
        "quotes": [{"text": " ".join(["слово"] * 16), "source_url": "https://x"}],
        "private_info_used": True,
        "hero_consent": False,
    }
    r = check_card(card, {"https://x"})
    assert len(r.errors) == 3


def test_facts_used_must_come_from_card():
    card = {"facts": [{"source_url": "https://x"}], "quotes": []}
    assert check_facts_used(["https://x"], card).ok
    assert not check_facts_used(["https://made-up"], card).ok
