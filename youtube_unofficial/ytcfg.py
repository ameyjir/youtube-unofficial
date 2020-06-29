from typing import Any, Dict, Mapping, cast
import json
import re

from bs4 import BeautifulSoup as Soup

from .constants import WATCH_LATER_URL
from .typing.ytcfg import YtcfgDict


def find_ytcfg(soup: Soup) -> YtcfgDict:
    return cast(
        YtcfgDict,
        json.JSONDecoder().raw_decode(
            re.sub(
                r'.+ytcfg.set\(\{', '{',
                list(
                    filter(
                        lambda x: '"INNERTUBE_CONTEXT_CLIENT_VERSION":' in x.
                        text, soup.select('script')))[0].text.strip()))[0])


def ytcfg_headers(ytcfg: YtcfgDict) -> Dict[str, str]:
    return {
        'x-spf-previous': WATCH_LATER_URL,
        'x-spf-referer': WATCH_LATER_URL,
        'x-youtube-client-name': str(ytcfg['INNERTUBE_CONTEXT_CLIENT_NAME']),
        'x-youtube-client-version': ytcfg['INNERTUBE_CONTEXT_CLIENT_VERSION'],
        'x-youtube-identity-token': ytcfg['ID_TOKEN'],
        'x-youtube-page-cl': str(ytcfg['PAGE_CL']),
        'x-youtube-utc-offset': '-240',
        'x-youtube-variants-checksum': ytcfg['VARIANTS_CHECKSUM'],
    }
