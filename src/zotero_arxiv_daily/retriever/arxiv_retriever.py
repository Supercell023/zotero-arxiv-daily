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
from loguru import logger
import time

PDF_EXTRACT_TIMEOUT = 180
@register_retriever("arxiv")
class ArxivRetriever(BaseRetriever):
    def __init__(self, config):
        super().__init__(config)
        if self.config.source.arxiv.category is None:
            raise ValueError("category must be specified for arxiv.")
    def _fetch_batch_with_retry(self, client, all_paper_ids, start_idx, end_idx, max_retries=5, base_delay=2):
        """使用指数退避重试获取一批论文"""
        batch_ids = all_paper_ids[start_idx:end_idx]
        
        for attempt in range(max_retries):
            try:
                search = arxiv.Search(id_list=batch_ids)
                batch = list(client.results(search))
                return batch
            except arxiv.HTTPError as e:
                # Extract status code from HTTPError
                # HTTPError message format: "Page request resulted in HTTP XXX (url)"
                status_code = None
                try:
                    # Try to extract status code from error message
                    error_msg = str(e)
                    if "HTTP" in error_msg:
                        # Extract the status code from the error message
                        parts = error_msg.split("HTTP")
                        if len(parts) > 1:
                            status_str = parts[1].strip().split()[0]
                            status_code = int(status_str)
                except (ValueError, IndexError):
                    pass
                
                # If we couldn't extract, check if the exception has args
                if status_code is None and hasattr(e, 'args') and len(e.args) > 1:
                    try:
                        status_code = int(e.args[1])
                    except (ValueError, IndexError, TypeError):
                        pass
                
                # Default to 429 if we can't determine the status code
                if status_code is None:
                    status_code = 429
                
                if status_code in [429, 503]:  # 速率限制或服务不可用
                    delay = base_delay * (2 ** attempt)  # 指数退避
                    if attempt < max_retries - 1:
                        logger.warning(f"arXiv API HTTP {status_code}。等待 {delay} 秒后重试... (第 {attempt + 1}/{max_retries} 次)")
                        time.sleep(delay)
                    else:
                        logger.error(f"在 {max_retries} 次重试后仍然收到 HTTP {status_code}")
                        raise
                else:
                    raise
            except Exception as e:
                # Handle other potential errors like connection errors
                if attempt < max_retries - 1:
                    delay = base_delay * (2 ** attempt)
                    logger.warning(f"获取批次时出错: {type(e).__name__}: {e}。等待 {delay} 秒后重试... (第 {attempt + 1}/{max_retries} 次)")
                    time.sleep(delay)
                else:
                    logger.error(f"在 {max_retries} 次重试后失败: {type(e).__name__}: {e}")
                    raise
                    
        return []
        
    def _retrieve_raw_papers(self) -> list[ArxivResult]:
        client = arxiv.Client(num_retries=10, delay_seconds=10)
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
        for i in range(0, len(all_paper_ids), 20):
            batch = self._fetch_batch_with_retry(client, all_paper_ids, i, min(i+20, len(all_paper_ids)))
            bar.update(len(batch))
            raw_papers.extend(batch)
            # 批次间添加延迟以避免速率限制
            if i + 20 < len(all_paper_ids):
                time.sleep(5)  # 在批次之间等待 5 秒（增加延迟以更好地处理速率限制）
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
