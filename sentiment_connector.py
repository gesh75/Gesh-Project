"""Shim — lives in gesh-core (consensus Phase 5).

Implementation is ``gesh_core.sentiment_connector`` (shared with the engine). This
stub makes ``import sentiment_connector`` resolve here too, so this repo's
node_news() can use live Reddit sentiment (praw → Reddit JSON API → keyword
scoring; 90-min cache, cwd-relative or GESH_SENTIMENT_CACHE). praw + nltk VADER are
optional — it degrades gracefully via requests.

    import sentiment_connector
    from sentiment_connector import node_sentiment, get_reddit_sentiment
"""

import sys

from gesh_core import sentiment_connector as _impl

sys.modules[__name__] = _impl
