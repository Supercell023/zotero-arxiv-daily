import arxiv
import feedparser
import pytest

from zotero_arxiv_daily.retriever import arxiv_retriever
from zotero_arxiv_daily.retriever.arxiv_retriever import ArxivRetriever


class FakeClient:
    def __init__(self, results):
        self.results_to_return = results
        self.calls = 0

    def results(self, search):
        self.calls += 1
        result = self.results_to_return(self.calls, search.id_list)
        if isinstance(result, Exception):
            raise result
        return iter(result)


def http_error(status):
    return arxiv.HTTPError("https://export.arxiv.org/api/query", 0, status)


def test_arxiv_retry_is_bounded_and_uses_jitter(monkeypatch):
    retriever = object.__new__(ArxivRetriever)
    sleeps = []
    client = FakeClient(
        lambda call, ids: http_error(429) if call < 3 else ids
    )
    monkeypatch.setattr(arxiv_retriever.random, "uniform", lambda _a, _b: 0)
    monkeypatch.setattr(arxiv_retriever.time, "sleep", sleeps.append)

    result = retriever._fetch_ids_with_retry(
        client, ["1", "2"], max_retries=3, base_delay=2
    )

    assert result == ["1", "2"]
    assert client.calls == 3
    assert sleeps == [2, 4]


def test_arxiv_batch_falls_back_to_smaller_batches(monkeypatch):
    retriever = object.__new__(ArxivRetriever)
    seen_batches = []

    def fetch(_client, ids):
        seen_batches.append(ids)
        if len(ids) > 5:
            raise http_error(429)
        return ids

    monkeypatch.setattr(retriever, "_fetch_ids_with_retry", fetch)

    result = retriever._fetch_batch_with_fallback(None, list("abcdefghij"))

    assert result == list("abcdefghij")
    assert [len(batch) for batch in seen_batches] == [10, 5, 5]


def test_arxiv_batch_does_not_split_non_retryable_errors(monkeypatch):
    retriever = object.__new__(ArxivRetriever)
    monkeypatch.setattr(
        retriever,
        "_fetch_ids_with_retry",
        lambda _client, _ids: (_ for _ in ()).throw(http_error(400)),
    )

    with pytest.raises(arxiv.HTTPError):
        retriever._fetch_batch_with_fallback(None, list("abcdefghij"))

def test_arxiv_retriever(config, monkeypatch):

    parsed_result = feedparser.parse("tests/retriever/arxiv_rss_example.xml")
    raw_parser = feedparser.parse
    def mock_feedparser_parse(url):
        if url == f"https://rss.arxiv.org/atom/{'+'.join(config.source.arxiv.category)}":
            return parsed_result
        return raw_parser(url)
    monkeypatch.setattr(feedparser, "parse", mock_feedparser_parse)
    
    retriever = ArxivRetriever(config)
    papers = retriever.retrieve_papers()
    parsed_results = [i for i in parsed_result.entries if i.get("arxiv_announce_type","new") == 'new']
    assert len(papers) == len(parsed_results)
    paper_titles = [i.title for i in papers]
    parsed_titles = [i.title for i in parsed_results]
    assert set(paper_titles) == set(parsed_titles)
