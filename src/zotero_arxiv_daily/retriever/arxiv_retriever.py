from .base import BaseRetriever, register_retriever
import arxiv
from arxiv import Result as ArxivResult
from ..protocol import Paper
from ..utils import extract_markdown_from_pdf, extract_tex_code_from_tar
from tempfile import TemporaryDirectory
from concurrent.futures import ThreadPoolExecutor, TimeoutError
import feedparser
from urllib.request import urlretrieve
from tqdm import tqdm
import os
import random
from loguru import logger
import time

PDF_EXTRACT_TIMEOUT = 180
ARXIV_BATCH_SIZE = 20
ARXIV_MIN_BATCH_SIZE = 5
ARXIV_MAX_RETRIES = 3
ARXIV_RETRY_BASE_DELAY = 10
ARXIV_RETRY_MAX_DELAY = 120
ARXIV_RETRY_JITTER = 0.25

@register_retriever("arxiv")
class ArxivRetriever(BaseRetriever):
    def __init__(self, config):
        super().__init__(config)
        if self.config.source.arxiv.category is None:
            raise ValueError("category must be specified for arxiv.")
    def _fetch_ids_with_retry(
        self,
        client,
        batch_ids,
        max_retries=ARXIV_MAX_RETRIES,
        base_delay=ARXIV_RETRY_BASE_DELAY,
    ):
        """Fetch one ID batch with one bounded retry layer."""
        for attempt in range(max_retries):
            try:
                search = arxiv.Search(id_list=batch_ids)
                return list(client.results(search))
            except arxiv.HTTPError as e:
                status_code = e.status
                if status_code not in (429, 503) or attempt == max_retries - 1:
                    raise

                backoff = min(base_delay * (2 ** attempt), ARXIV_RETRY_MAX_DELAY)
                delay = backoff + random.uniform(0, backoff * ARXIV_RETRY_JITTER)
                logger.warning(
                    f"arXiv API HTTP {status_code}; retrying in {delay:.1f}s "
                    f"({attempt + 1}/{max_retries - 1})"
                )
                time.sleep(delay)

    def _fetch_batch_with_fallback(self, client, batch_ids):
        """Fetch IDs, splitting a throttled batch until the minimum size."""
        try:
            return self._fetch_ids_with_retry(client, batch_ids)
        except arxiv.HTTPError as e:
            if e.status not in (429, 503) or len(batch_ids) <= ARXIV_MIN_BATCH_SIZE:
                raise

            midpoint = len(batch_ids) // 2
            left_ids = batch_ids[:midpoint]
            right_ids = batch_ids[midpoint:]
            logger.warning(
                f"arXiv API HTTP {e.status} persisted for {len(batch_ids)} IDs; "
                f"falling back to batches of {len(left_ids)} and {len(right_ids)}"
            )
            return (
                self._fetch_batch_with_fallback(client, left_ids)
                + self._fetch_batch_with_fallback(client, right_ids)
            )
        
    def _retrieve_raw_papers(self) -> list[ArxivResult]:
        # Retry here rather than stacking our retries on arxiv.Client retries.
        # The client still enforces a three-second interval between requests.
        client = arxiv.Client(num_retries=0, delay_seconds=3)
        query = '+'.join(self.config.source.arxiv.category)
        include_cross_list = self.config.source.arxiv.get("include_cross_list", False)
        # 从 arxiv rss feed 获取最新论文
        feed = feedparser.parse(f"https://rss.arxiv.org/atom/{query}")
        if 'Feed error for query' in feed.feed.title:
            raise Exception(f"Invalid ARXIV_QUERY: {query}.")
        raw_papers = []
        allowed_announce_types = {"new", "cross"} if include_cross_list else {"new"}
        all_paper_ids = [
                i.id.removeprefix("oai:arXiv.org:")
                for i in feed.entries
                if i.get("arxiv_announce_type", "new") in allowed_announce_types
            ]
        if self.config.executor.debug:
            all_paper_ids = all_paper_ids[:10]
                
            # 使用重试逻辑获取每批论文的完整信息
        bar = tqdm(total=len(all_paper_ids))
        for i in range(0, len(all_paper_ids), ARXIV_BATCH_SIZE):
            batch_ids = all_paper_ids[i:i + ARXIV_BATCH_SIZE]
            batch = self._fetch_batch_with_fallback(client, batch_ids)
            bar.update(len(batch))
            raw_papers.extend(batch)
        bar.close()

        return raw_papers

    def convert_to_paper(self, raw_paper:ArxivResult) -> Paper:
        title = raw_paper.title
        authors = [a.name for a in raw_paper.authors]
        abstract = raw_paper.summary
        pdf_url = raw_paper.pdf_url

        # Check if PDF extraction should be skipped
        skip_pdf = self.config.source.arxiv.get("skip_pdf_extraction", False)
        pre_filter_enabled = self.config.executor.get('pre_filter_num', None) is not None

        # Skip PDF if explicitly disabled OR if pre-filtering is enabled (will extract later)
        if skip_pdf or pre_filter_enabled:
            full_text = None
        else:
            try:
                with ThreadPoolExecutor(max_workers=1) as pool:
                    full_text = pool.submit(extract_text_from_pdf, raw_paper).result(timeout=PDF_EXTRACT_TIMEOUT)
            except TimeoutError:
                logger.warning(f"PDF extraction timed out for {raw_paper.title}")
                full_text = None
            if full_text is None:
                full_text = extract_text_from_tar(raw_paper)

        # Remove trailing colon from entry_id if present
        paper_url = raw_paper.entry_id.rstrip(':')

        paper = Paper(
            source=self.name,
            title=title,
            authors=authors,
            abstract=abstract,
            url=paper_url,
            pdf_url=pdf_url,
            full_text=full_text
        )

        # Store raw_paper for later PDF extraction if needed
        if pre_filter_enabled:
            paper._raw_paper = raw_paper

        return paper

    def extract_full_text(self, paper: Paper) -> str:
        """Extract full text from a paper's stored raw data"""
        if not hasattr(paper, '_raw_paper'):
            return None

        raw_paper = paper._raw_paper
        try:
            with ThreadPoolExecutor(max_workers=1) as pool:
                full_text = pool.submit(extract_text_from_pdf, raw_paper).result(timeout=PDF_EXTRACT_TIMEOUT)
        except TimeoutError:
            logger.warning(f"PDF extraction timed out for {paper.title}")
            full_text = None
        if full_text is None:
            full_text = extract_text_from_tar(raw_paper)

        return full_text

def extract_text_from_pdf(paper: ArxivResult) -> str | None:
    with TemporaryDirectory() as temp_dir:
        path = os.path.join(temp_dir, "paper.pdf")
        if paper.pdf_url is None:
            logger.warning(f"No PDF URL available for {paper.title}")
            return None
        urlretrieve(paper.pdf_url, path)
        try:
            full_text = extract_markdown_from_pdf(path)
        except Exception as e:
            logger.warning(f"Failed to extract full text of {paper.title} from pdf: {e}")
            full_text = None
        return full_text

def extract_text_from_tar(paper: ArxivResult) -> str | None:
    with TemporaryDirectory() as temp_dir:
        path = os.path.join(temp_dir, "paper.tar.gz")
        source_url = paper.source_url()
        if source_url is None:
            logger.warning(f"No source URL available for {paper.title}")
            return None
        urlretrieve(source_url, path)
        try:
            file_contents = extract_tex_code_from_tar(path, paper.entry_id)
            if "all" not in file_contents:
                logger.warning(f"Failed to extract full text of {paper.title} from tar: Main tex file not found.")
                return None
            full_text = file_contents["all"]
        except Exception as e:
            logger.warning(f"Failed to extract full text of {paper.title} from tar: {e}")
            full_text = None
        return full_text
