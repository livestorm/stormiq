"""Labelling a chat with a model (`ai/chat_labelling.py`), on synthetic messages.

The model is replaced by a fake `llm_client.generate_json` that records each
request and answers from a script, so no test makes a call. What is checked:
what is sent (ids, team flag, text; never an author), the batches and their
shared prefix and schema, the laughter override, and that a run either labels
every message or returns nothing.
"""

import pytest

from livestorm_app import llm_client
from livestorm_app.ai import chat_labelling, jobs
from livestorm_app.ai.chat_labelling import ChatLabelBatch, label_chat
from livestorm_app.ai.request import anthropic_call
from livestorm_app.ai.usage import CallContext
from livestorm_app.record.chat_labels import chat_hash

CTX = CallContext(job_kind="chat_labels", session_id="sess-1", organization_id="org-1", job_id="job-1")


def _messages(count, start=1):
    return [
        {
            "eid": f"C{n:04d}",
            "livestorm_id": f"msg-{n}",
            "text": f"Synthetic message {n}\nwith a second line",
            "is_team": n % 10 == 0,
            "author_ref": f"P-{n:08x}",
            "author_id": f"people-{n}",
            "minute": n % 45,
        }
        for n in range(start, start + count)
    ]


class FakeModel:
    """Answers each batch with one label per line it was sent, unless told otherwise."""

    def __init__(self, label="content", rewrite=None):
        self.requests = []
        self.schemas = []
        self.label = label
        self.rewrite = rewrite

    def __call__(self, kind, schema, request, *, api_key, ctx):
        assert kind == "chat_labels" and ctx is CTX and api_key == "key-1"
        self.requests.append(request)
        self.schemas.append(schema)
        ids = [line.split("\t")[0] for line in request.payload.splitlines()]
        items = [{"id": label_id, "label": self.label, "question": None, "language": "en"} for label_id in ids]
        if self.rewrite:
            items = self.rewrite(items)
        return ChatLabelBatch.model_validate({"labels": items})


@pytest.fixture
def model(monkeypatch):
    fake = FakeModel()
    monkeypatch.setattr(llm_client, "generate_json", fake)
    monkeypatch.setattr(llm_client, "get_provider", lambda: "anthropic")
    return fake


def test_every_message_is_labelled_in_id_order(model):
    messages = list(reversed(_messages(5)))
    result = label_chat(messages, api_key="key-1", ctx=CTX)
    assert [label["id"] for label in result.labels] == ["C0001", "C0002", "C0003", "C0004", "C0005"]
    assert result.labels[0] == {"id": "C0001", "livestorm_id": "msg-1", "class": "content", "question": None, "language": "en"}
    assert result.chat_hash == chat_hash(messages)
    assert result.model == jobs.resolve_model("chat_labels").model_id == "claude-haiku-5-5"


def test_only_the_id_the_team_flag_and_the_text_are_sent(model):
    label_chat(_messages(10), api_key="key-1", ctx=CTX)
    lines = model.requests[0].payload.splitlines()
    assert lines[0] == "C0001\taudience\tSynthetic message 1 with a second line"
    assert lines[9] == "C0010\tteam\tSynthetic message 10 with a second line"
    sent = model.requests[0].payload + model.requests[0].instructions
    for secret in ("P-00000001", "people-1", "msg-1"):
        assert secret not in sent, "never an author, a people id or a Livestorm id"


def test_a_long_message_is_cut_before_it_is_sent(model):
    messages = [{"eid": "C0001", "livestorm_id": "m", "text": "word " * 400, "is_team": False}]
    label_chat(messages, api_key="key-1", ctx=CTX)
    text = model.requests[0].payload.split("\t")[2]
    assert len(text) <= chat_labelling.MAX_TEXT_CHARS + 1 and text.endswith("…")


def test_batches_share_one_cached_prefix_and_one_schema(model):
    label_chat(_messages(chat_labelling.BATCH_SIZE * 2 + 1), api_key="key-1", ctx=CTX)
    assert [len(request.payload.splitlines()) for request in model.requests] == [150, 150, 1]
    assert [(request.shared_calls, request.call_number) for request in model.requests] == [(3, 1), (3, 2), (3, 3)]
    assert all(request.cached for request in model.requests)
    assert [request.expects_cache_read for request in model.requests] == [False, True, True]
    assert set(model.schemas) == {ChatLabelBatch}, "a different schema would break the cached prefix"
    assert len({request.instructions for request in model.requests}) == 1

    caps = jobs.resolve_model("chat_labels").caps
    sent = [anthropic_call(request, caps, 8_000, None).params for request in model.requests]
    assert sent[0]["system"] == sent[1]["system"] == sent[2]["system"], "the cached prefix is byte for byte the same"
    assert "cache_control" in sent[0]["system"][-1]


def test_one_batch_writes_no_cache(model):
    label_chat(_messages(3), api_key="key-1", ctx=CTX)
    assert len(model.requests) == 1 and not model.requests[0].cached


def test_the_prompt_is_long_enough_to_cache():
    caps = jobs.resolve_model("chat_labels", "anthropic").caps
    assert len(chat_labelling.load_instructions()) // 4 > caps.min_cache_tokens


def test_an_empty_chat_makes_no_call(model):
    result = label_chat([], api_key="key-1", ctx=CTX)
    assert (result.labels, result.chat_hash, model.requests) == ([], chat_hash([]), [])


@pytest.mark.parametrize("text", ["haha", "HAHAHA!", "lol", "mdr", "ptdr xD", "😂😂", "hihi"])
def test_a_laughter_only_message_is_laughter_whatever_the_model_said(model, text):
    model.label = "question"
    model.rewrite = lambda items: [dict(item, question="content") for item in items]
    result = label_chat([{"eid": "C0001", "livestorm_id": "m", "text": text, "is_team": False}], api_key="key-1", ctx=CTX)
    assert (result.labels[0]["class"], result.labels[0]["question"]) == ("laughter", None)


@pytest.mark.parametrize("text", ["ha", "haha that is a great point", "lolita", "Mdr Corp"])
def test_laughter_inside_a_sentence_keeps_the_models_label(model, text):
    result = label_chat([{"eid": "C0001", "livestorm_id": "m", "text": text, "is_team": False}], api_key="key-1", ctx=CTX)
    assert result.labels[0]["class"] == "content"


def test_a_kind_is_kept_only_on_a_question(model):
    model.rewrite = lambda items: [dict(items[0], label="question", question="logistics"), dict(items[1], question="content")]
    result = label_chat(_messages(2), api_key="key-1", ctx=CTX)
    assert [(label["class"], label["question"]) for label in result.labels] == [("question", "logistics"), ("content", None)]


@pytest.mark.parametrize(
    "rewrite, reason",
    [
        (lambda items: items[:-1], "1 missing"),
        (lambda items: items + [dict(items[0], id="C9999")], "1 unknown"),
        (lambda items: items + [items[0]], "appears twice"),
        (lambda items: [dict(items[0], label="question", question=None)] + items[1:], "needs its kind"),
    ],
)
def test_an_incomplete_or_malformed_answer_fails_the_whole_run(model, rewrite, reason):
    calls = []

    def second_batch_only(items):
        calls.append(1)
        return rewrite(items) if len(calls) == 2 else items

    model.rewrite = second_batch_only
    with pytest.raises(llm_client.LLMInvalidOutput, match=f"batch 2 of 2: .*{reason}"):
        label_chat(_messages(chat_labelling.BATCH_SIZE + 5), api_key="key-1", ctx=CTX)


def test_a_model_error_is_not_caught(model, monkeypatch):
    def refuse(*args, **kwargs):
        raise llm_client.LLMRefused("refused")

    monkeypatch.setattr(llm_client, "generate_json", refuse)
    with pytest.raises(llm_client.LLMRefused):
        label_chat(_messages(2), api_key="key-1", ctx=CTX)


def test_a_language_must_be_an_iso_code():
    with pytest.raises(ValueError):
        ChatLabelBatch.model_validate({"labels": [{"id": "C0001", "label": "social", "question": None, "language": "English"}]})
