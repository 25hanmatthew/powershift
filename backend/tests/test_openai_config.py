from backend.openai_config import openai_model, response_settings


def test_astra_is_default_for_all_text_workloads(monkeypatch):
    monkeypatch.delenv('OPENAI_MODEL', raising=False)
    monkeypatch.delenv('OPENAI_CHAT_MODEL', raising=False)
    assert openai_model() == openai_model(chat=True) == 'gpt-6-astra'
    assert response_settings(openai_model(), 2200) == {
        'model': 'gpt-6-astra', 'reasoning': {'effort': 'low'}, 'max_output_tokens': 8192}


def test_model_overrides_keep_their_output_contract(monkeypatch):
    monkeypatch.setenv('OPENAI_MODEL', 'gpt-4.1-mini')
    monkeypatch.setenv('OPENAI_CHAT_MODEL', 'gpt-6-astra')
    assert openai_model() == 'gpt-4.1-mini'
    assert openai_model(chat=True) == 'gpt-6-astra'
    assert response_settings(openai_model(), 2600) == {'model': 'gpt-4.1-mini', 'max_output_tokens': 2600}
    monkeypatch.setenv('OPENAI_CHAT_MODEL', '')
    assert openai_model(chat=True) == 'gpt-4.1-mini'
