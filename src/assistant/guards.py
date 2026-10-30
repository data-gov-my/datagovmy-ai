"""Output guards for streamed answers.

The code (and every prompt) is public, so these checks don't rely on hidden
rules. They compare the answer against runtime values an attacker can't
change: the retrieved documentation and the user's own text. Knowing how they
work doesn't help, because passing them needs the text or link to be in
data.gov.my's own docs.

- Links: only official domains, or URLs that appear verbatim in the
  retrieved docs. Other links are removed (stops phishing or scam links
  smuggled through a docs-looking question).
- Echo: if the answer reproduces a quoted phrase from the user that isn't in
  the retrieved docs, the answer is stopped (stops "how do I query the
  dataset called '<statement>'" style attacks that get the bot to repeat an
  attacker-chosen statement inside an otherwise normal answer).

Text is released at sentence/line boundaries so the stream stays live while
each piece is checked before the user sees it.
"""

import logging
import re
from typing import Iterable, List
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

ALLOWED_LINK_DOMAINS = ("data.gov.my", "dosm.gov.my")  # incl. subdomains
MIN_ECHO_WORDS = 3

URL_RE = re.compile(r"https?://[^\s<>()\[\]\"'`]+")
MD_LINK_RE = re.compile(r"\[([^\]]*)\]\((https?://[^)\s]+)\)")
# double and curly quotes only: single quotes clash with apostrophes
QUOTED_RE = re.compile(r"[\"“”«»]([^\"“”«»\n]{1,300})[\"“”«»]")
BOUNDARY_RE = re.compile(r"(\n|[.!?](?=\s))")


def normalise(text: str) -> str:
    return " ".join(re.sub(r"[^\w\s]", " ", text.lower()).split())


def quoted_spans(texts: Iterable[str]) -> List[str]:
    spans = []
    for text in texts:
        for span in QUOTED_RE.findall(text):
            span = normalise(span)
            if len(span.split()) >= MIN_ECHO_WORDS:
                spans.append(span)
    return spans


def is_allowed_url(url: str, context: str) -> bool:
    url = url.rstrip(".,;:!?")
    host = (urlparse(url).hostname or "").lower()
    if any(host == d or host.endswith("." + d) for d in ALLOWED_LINK_DOMAINS):
        return True
    return url in context


class AnswerGuard:
    """Feed streamed tokens in, get checked text out"""

    def __init__(self, user_texts: Iterable[str], context: str = ""):
        self.context = context
        self.context_normalised = normalise(context)
        self.spans = [s for s in quoted_spans(user_texts)]
        self.released = ""
        self.pending = ""
        self.blocked = False

    def set_context(self, context: str) -> None:
        self.context = context
        self.context_normalised = normalise(context)

    def feed(self, token: str) -> str:
        if self.blocked:
            return ""
        self.pending += token
        boundaries = list(BOUNDARY_RE.finditer(self.pending))
        if not boundaries:
            return ""
        cut = boundaries[-1].end()
        chunk, self.pending = self.pending[:cut], self.pending[cut:]
        return self._check(chunk)

    def finish(self) -> str:
        chunk, self.pending = self.pending, ""
        return self._check(chunk) if chunk and not self.blocked else ""

    def _check(self, chunk: str) -> str:
        answer = normalise(self.released + chunk)
        for span in self.spans:
            if span in answer and span not in self.context_normalised:
                logger.warning("Answer echoed user-quoted text, stopping: %r", span)
                self.blocked = True
                return ""
        chunk = self._strip_links(chunk)
        self.released += chunk
        return chunk

    def _strip_links(self, chunk: str) -> str:
        def md_link(match):
            text, url = match.group(1), match.group(2)
            if is_allowed_url(url, self.context):
                return match.group(0)
            logger.warning("Removed link not in allowlist or docs: %s", url)
            return text

        def bare_url(match):
            url = match.group(0)
            if is_allowed_url(url, self.context):
                return url
            logger.warning("Removed link not in allowlist or docs: %s", url)
            return "[link removed]"

        chunk = MD_LINK_RE.sub(md_link, chunk)
        # bare urls outside markdown links (markdown targets were handled above)
        parts = re.split(r"(\[[^\]]*\]\(https?://[^)\s]+\))", chunk)
        return "".join(
            part if MD_LINK_RE.fullmatch(part) else URL_RE.sub(bare_url, part)
            for part in parts
        )
