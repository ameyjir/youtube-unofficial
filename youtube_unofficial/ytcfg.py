from typing import Dict, cast
import json
import re

from bs4 import BeautifulSoup as Soup

from .constants import WATCH_LATER_URL
from .typing.ytcfg import YtcfgDict
from .util import first


def find_ytcfg(soup: Soup) -> YtcfgDict:
    return cast(
        YtcfgDict,
        first(json.JSONDecoder().raw_decode(
            re.sub(
                r'.+ytcfg.set\(\{', '{',
                first(x for x in soup.select('script')
                      if '"INNERTUBE_CONTEXT_CLIENT_VERSION":' in
                      x.text).text.strip()))))


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
