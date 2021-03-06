from datetime import datetime
from http.cookiejar import CookieJar, LoadError, MozillaCookieJar
from os.path import expanduser
from typing import (Any, Iterable, Iterator, Mapping, Optional, Sequence, Type,
                    cast)
import hashlib
import json
import logging

from typing_extensions import Final
import requests

from .comment import (DEFAULT_DELETE_ACTION_PATH, CommentHistoryEntry,
                      make_comment_history_entry)
from .constants import (BROWSE_AJAX_URL, COMMENT_HISTORY_URL,
                        COMMUNITY_HISTORY_URL, HISTORY_URL, HOMEPAGE_URL,
                        LIVE_CHAT_HISTORY_URL, SEARCH_HISTORY_URL,
                        SERVICE_AJAX_URL, USER_AGENT, WATCH_HISTORY_URL,
                        WATCH_LATER_URL)
from .download import DownloadMixin
from .exceptions import AuthenticationError, UnexpectedError
from .initial import initial_data, initial_guide_data
from .live_chat import LiveChatHistoryEntry, make_live_chat_history_entry
from .login import YouTubeLogin
from .typing import HasStringCode
from .typing.browse_ajax import BrowseAJAXSequence
from .typing.guide_data import SectionItemDict
from .typing.playlist import PlaylistInfo
from .typing.ytcfg import YtcfgDict
from .util import context_client_body, first, path as at_path
from .ytcfg import find_ytcfg, ytcfg_headers

__all__ = ('YouTube', )


class YouTube(DownloadMixin):
    def __init__(self,
                 username: Optional[str] = None,
                 password: Optional[str] = None,
                 netrc_file: Optional[str] = None,
                 cookies_path: Optional[str] = None,
                 logged_in: bool = False,
                 cookiejar_cls: Type[CookieJar] = MozillaCookieJar):
        if not netrc_file:
            self.netrc_file = expanduser('~/.netrc')
        else:
            self.netrc_file = netrc_file
        if not cookies_path:
            cookies_path = expanduser('~/.config/ytch-cookies.txt')
        self.username = username
        self.password = password
        self._log: Final = logging.getLogger('youtube-unofficial')
        self._favorites_playlist_id: Optional[str] = None
        self._sess = requests.Session()
        self._init_cookiejar(cookies_path, cls=cookiejar_cls)
        self._sess.cookies = self._cj  # type: ignore[assignment]
        self._sess.headers.update({
            'User-Agent': USER_AGENT,
        })
        self._login_handler = YouTubeLogin(self._sess, self._cj, username)
        if logged_in:
            self._login_handler._logged_in = True  # pylint: disable=protected-access

    @property
    def _logged_in(self):
        return self._login_handler._logged_in  # pylint: disable=protected-access

    def _init_cookiejar(self,
                        path: str,
                        cls: Type[CookieJar] = MozillaCookieJar) -> None:
        self._log.debug('Initialising cookie jar (%s) at %s', cls.__name__,
                        path)
        try:
            with open(path):
                pass
        except IOError:
            with open(path, 'w+'):
                pass
        try:
            self._cj = cls(path)  # type: ignore[arg-type]
        except TypeError:
            self._cj = cls()
        if hasattr(self._cj, 'load'):
            try:
                self._cj.load()  # type: ignore[attr-defined]
            except LoadError:
                self._log.debug('File %s for cookies does not yet exist', path)

    def login(self) -> None:
        self._login_handler.login()

    def remove_set_video_id_from_playlist(
            self,
            playlist_id: str,
            set_video_id: str,
            csn: Optional[str] = None,
            headers: Optional[Mapping[str, str]] = None,
            xsrf_token: Optional[str] = None) -> None:
        """Removes a video from a playlist. The set_video_id is NOT the same as
        the video ID."""
        if not self._logged_in:
            raise AuthenticationError('This method requires a call to '
                                      'login() first')
        if not headers or not csn or not xsrf_token:
            soup = self._download_page_soup(WATCH_LATER_URL)
            ytcfg = find_ytcfg(soup)
            headers = ytcfg_headers(ytcfg)
        data = cast(
            HasStringCode,
            self._download_page(SERVICE_AJAX_URL,
                                method='post',
                                data={
                                    'sej':
                                    json.dumps({
                                        'clickTrackingParams': '',
                                        'commandMetadata': {
                                            'webCommandMetadata': {
                                                'url': '/service_ajax',
                                                'sendPost': True
                                            }
                                        },
                                        'playlistEditEndpoint': {
                                            'playlistId':
                                            playlist_id,
                                            'actions': [{
                                                'setVideoId':
                                                set_video_id,
                                                'action':
                                                'ACTION_REMOVE_VIDEO'
                                            }],
                                            'params':
                                            'CAE%3D',
                                            'clientActions': [{
                                                'playlistRemoveVideosAction': {
                                                    'setVideoIds':
                                                    [set_video_id]
                                                }
                                            }]
                                        }
                                    }),
                                    'csn':
                                    csn or ytcfg['EVENT_ID'],
                                    'session_token':
                                    xsrf_token or ytcfg['XSRF_TOKEN']
                                },
                                params={'name': 'playlistEditEndpoint'},
                                return_json=True,
                                headers=headers))
        if data['code'] != 'SUCCESS':
            raise UnexpectedError(
                'Failed to delete video from Watch Later playlist')

    def clear_watch_history(self) -> None:
        """Clears watch history."""
        if not self._logged_in:
            raise AuthenticationError('This method requires a call to '
                                      'login() first')

        content = self._download_page_soup(HISTORY_URL)
        ytcfg = find_ytcfg(content)
        headers = ytcfg_headers(ytcfg)
        headers['x-spf-previous'] = HISTORY_URL
        headers['x-spf-referer'] = HISTORY_URL
        init_data = initial_data(content)
        params = {'name': 'feedbackEndpoint'}
        try:
            data = {
                'sej':
                json.dumps(
                    init_data['contents']['twoColumnBrowseResultsRenderer']
                    ['secondaryContents']['browseFeedActionsRenderer']
                    ['contents'][2]['buttonRenderer']['navigationEndpoint']
                    ['confirmDialogEndpoint']['content']
                    ['confirmDialogRenderer']['confirmButton']
                    ['buttonRenderer']['serviceEndpoint']),
                'csn':
                ytcfg['EVENT_ID'],
                'session_token':
                ytcfg['XSRF_TOKEN']
            }
        except KeyError:
            self._log.debug('Clear button is likely disabled. History is '
                            'likely empty')
            return
        self._download_page(SERVICE_AJAX_URL,
                            params=params,
                            data=data,
                            headers=headers,
                            return_json=True,
                            method='post')
        self._log.info('Successfully cleared history')

    def get_favorites_playlist_id(self) -> str:
        """Get the Favourites playlist ID."""
        if not self._logged_in:
            raise AuthenticationError('This method requires a call to '
                                      'login() first')
        if self._favorites_playlist_id:
            return self._favorites_playlist_id

        def check_section_items(
                items: Iterable[SectionItemDict]) -> Optional[str]:
            for item in items:
                if 'guideEntryRenderer' in item:
                    if (item['guideEntryRenderer']['icon']['iconType']
                        ) == 'LIKES_PLAYLIST':
                        return (item['guideEntryRenderer']['entryData']
                                ['guideEntryData']['guideEntryId'])
                elif 'guideCollapsibleEntryRenderer' in item:
                    renderer = item['guideCollapsibleEntryRenderer']
                    for e_item in renderer['expandableItems']:
                        if e_item['guideEntryRenderer']['icon'][
                                'iconType'] == 'LIKES_PLAYLIST':
                            return (e_item['guideEntryRenderer']['entryData']
                                    ['guideEntryData']['guideEntryId'])
            return None

        content = self._download_page_soup(HOMEPAGE_URL)
        gd = initial_guide_data(content)
        section_items = (
            gd['items'][0]['guideSectionRenderer']['items'][4]
            ['guideCollapsibleSectionEntryRenderer']['sectionItems'])

        found = check_section_items(section_items)
        if found:
            self._favorites_playlist_id = found
            return self._favorites_playlist_id

        expandable_items = (section_items[-1]['guideCollapsibleEntryRenderer']
                            ['expandableItems'])
        found = check_section_items(expandable_items)
        if not found:
            raise ValueError('Could not determine favourites playlist ID')

        self._favorites_playlist_id = found
        self._log.debug('Got favourites playlist ID: %s',
                        self._favorites_playlist_id)

        return self._favorites_playlist_id

    def clear_favorites(self) -> None:
        """Removes all videos from the Favourites playlist."""
        if not self._logged_in:
            raise AuthenticationError('This method requires a call to '
                                      'login() first')

        self.clear_playlist(self.get_favorites_playlist_id())

    def get_playlist_info(self, playlist_id: str) -> Iterator[PlaylistInfo]:
        """Get playlist information given a playlist ID."""
        if not self._logged_in:
            raise AuthenticationError('This method requires a call to '
                                      'login() first')

        url = 'https://www.youtube.com/playlist?list={}'.format(playlist_id)
        content = self._download_page_soup(url)
        ytcfg = find_ytcfg(content)
        headers = ytcfg_headers(ytcfg)
        yt_init_data = initial_data(content)

        video_list_renderer = (
            yt_init_data['contents']['twoColumnBrowseResultsRenderer']['tabs']
            [0]['tabRenderer']['content']['sectionListRenderer']['contents'][0]
            ['itemSectionRenderer']['contents'][0]['playlistVideoListRenderer']
        )
        try:
            yield from video_list_renderer['contents']
        except KeyError:
            yield from []

        next_cont = continuation = itct = None
        try:
            next_cont = video_list_renderer['continuations'][0][
                'nextContinuationData']
            continuation = next_cont['continuation']
            itct = next_cont['clickTrackingParams']
        except KeyError:
            pass

        if continuation and itct:
            while True:
                params = {
                    'ctoken': continuation,
                    'continuation': continuation,
                    'itct': itct
                }
                contents = cast(
                    BrowseAJAXSequence,
                    self._download_page(BROWSE_AJAX_URL,
                                        params=params,
                                        return_json=True,
                                        headers=headers))
                response = contents[1]['response']
                yield from (response['continuationContents']
                            ['playlistVideoListContinuation']['contents'])

                try:
                    continuations = (
                        response['continuationContents']
                        ['playlistVideoListContinuation']['continuations'])
                except KeyError:
                    break
                next_cont = continuations[0]['nextContinuationData']
                itct = next_cont['clickTrackingParams']
                continuation = next_cont['continuation']

    def clear_playlist(self, playlist_id: str) -> None:
        """
        Removes all videos from the specified playlist.

        Use `WL` for Watch Later.
        """
        if not self._logged_in:
            raise AuthenticationError('This method requires a call to '
                                      'login() first')

        playlist_info = self.get_playlist_info(playlist_id)
        url = 'https://www.youtube.com/playlist?list={}'.format(playlist_id)
        content = self._download_page_soup(url)
        ytcfg = find_ytcfg(content)
        headers = ytcfg_headers(ytcfg)
        csn = ytcfg['EVENT_ID']
        xsrf_token = ytcfg['XSRF_TOKEN']

        try:
            set_video_ids = list(
                map(lambda x: x['playlistVideoRenderer']['setVideoId'],
                    playlist_info))
        except KeyError:
            self._log.info('Caught KeyError. This probably means the playlist '
                           'is empty.')
            return

        for set_video_id in set_video_ids:
            self._log.debug('Deleting from playlist: set_video_id = %s',
                            set_video_id)
            self.remove_set_video_id_from_playlist(playlist_id,
                                                   set_video_id,
                                                   csn,
                                                   xsrf_token=xsrf_token,
                                                   headers=headers)

    def clear_watch_later(self) -> None:
        """Removes all videos from the 'Watch Later' playlist."""
        self.clear_playlist('WL')

    def remove_video_id_from_favorites(
            self,
            video_id: str,
            headers: Optional[Mapping[str, str]] = None) -> None:
        """Removes a video from Favourites by video ID."""
        playlist_id = self.get_favorites_playlist_id()
        playlist_info = self.get_playlist_info(playlist_id)
        url = 'https://www.youtube.com/playlist?list={}'.format(playlist_id)
        content = self._download_page_soup(url)
        ytcfg = find_ytcfg(content)
        headers = ytcfg_headers(ytcfg)

        try:
            entry = list(
                filter(
                    lambda x: (x['playlistVideoRenderer']['navigationEndpoint']
                               ['watchEndpoint']['videoId']) == video_id,
                    playlist_info))[0]
        except IndexError:
            return

        set_video_id = entry['playlistVideoRenderer']['setVideoId']

        self.remove_set_video_id_from_playlist(playlist_id,
                                               set_video_id,
                                               ytcfg['EVENT_ID'],
                                               xsrf_token=ytcfg['XSRF_TOKEN'],
                                               headers=headers)

    def get_history_info(self) -> Iterator[Mapping[str, Any]]:
        """Get information about the History playlist."""
        if not self._logged_in:
            raise AuthenticationError('This method requires a call to '
                                      'login() first')

        content = self._download_page_soup(HISTORY_URL)
        init_data = initial_data(content)
        ytcfg = find_ytcfg(content)
        headers = ytcfg_headers(ytcfg)

        section_list_renderer = (
            init_data['contents']['twoColumnBrowseResultsRenderer']['tabs'][0]
            ['tabRenderer']['content']['sectionListRenderer'])
        for section_list in section_list_renderer['contents']:
            yield from section_list['itemSectionRenderer']['contents']
        try:
            next_continuation = (section_list_renderer['continuations'][0]
                                 ['nextContinuationData'])
        except KeyError:
            return

        params = dict(
            continuation=next_continuation['continuation'],
            ctoken=next_continuation['continuation'],
            itct=next_continuation['clickTrackingParams'],
        )
        xsrf = ytcfg['XSRF_TOKEN']

        while True:
            resp = cast(
                BrowseAJAXSequence,
                self._download_page(BROWSE_AJAX_URL,
                                    return_json=True,
                                    headers=headers,
                                    data={'session_token': xsrf},
                                    method='post',
                                    params=params))
            contents = resp[1]['response']
            section_list_renderer = (
                contents['continuationContents']['sectionListContinuation'])
            for section_list in section_list_renderer['contents']:
                yield from section_list['itemSectionRenderer']['contents']

            try:
                continuations = section_list_renderer['continuations']
            except KeyError as e:
                # Probably the end of the history
                self._log.debug('Caught KeyError: %s. Possible keys: %s', e,
                                ', '.join(section_list_renderer.keys()))
                break
            xsrf = resp[1]['xsrf_token']
            next_cont = continuations[0]['nextContinuationData']
            params['itct'] = next_cont['clickTrackingParams']
            params['ctoken'] = next_cont['continuation']
            params['continuation'] = next_cont['continuation']

    def remove_video_id_from_history(self, video_id: str) -> bool:
        """Delete a history entry by video ID."""
        if not self._logged_in:
            raise AuthenticationError('This method requires a call to '
                                      'login() first')
        history_info = self.get_history_info()
        content = self._download_page_soup(HISTORY_URL)
        ytcfg = find_ytcfg(content)
        headers = ytcfg_headers(ytcfg)
        try:
            entry = first(x for x in history_info
                          if x['videoRenderer']['videoId'] == video_id)
        except IndexError:
            return False
        resp = cast(
            HasStringCode,
            self._download_page(SERVICE_AJAX_URL,
                                return_json=True,
                                data=dict(sej=json.dumps(
                                    entry['videoRenderer']['menu']
                                    ['menuRenderer']['topLevelButtons'][0]
                                    ['buttonRenderer']['serviceEndpoint']),
                                          csn=ytcfg['EVENT_ID'],
                                          session_token=ytcfg['XSRF_TOKEN']),
                                method='post',
                                headers=headers,
                                params=dict(name='feedbackEndpoint')))
        return resp['code'] == 'SUCCESS'

    def _authorization_sapisidhash_header(self) -> str:
        now = int(datetime.now().timestamp())
        sapisid: Optional[str] = None
        for cookie in self._cj:
            if cookie.name in ('SAPISID', '__Secure-3PAPISID'):
                sapisid = cookie.value
                break
        assert sapisid is not None
        m = hashlib.sha1()
        m.update(f'{now} {sapisid} https://www.youtube.com'.encode())
        return f'SAPISIDHASH {now}_{m.hexdigest()}'

    def _single_feedback_api_call(
            self,
            ytcfg: YtcfgDict,
            feedback_token: str,
            click_tracking_params: str = '',
            api_url: str = '/youtubei/v1/feedback') -> bool:
        return cast(
            Mapping[str, Any],
            self._download_page(
                f'https://www.youtube.com{api_url}',
                method='post',
                params=dict(key=ytcfg['INNERTUBE_API_KEY']),
                headers={
                    'Authority': 'www.youtube.com',
                    'Authorization': self._authorization_sapisidhash_header(),
                    'x-goog-authuser': '0',
                    'x-origin': 'https://www.youtube.com',
                },
                json=dict(context=dict(
                    clickTracking=dict(
                        clickTrackingParams=click_tracking_params),
                    client=context_client_body(ytcfg),
                    request=dict(consistencyTokenJars=[],
                                 internalExperimentFlags=[]),
                    user=dict(onBehalfOfUser=ytcfg['DELEGATED_SESSION_ID'])),
                          feedbackTokens=[feedback_token],
                          isFeedbackTokenUnencrypted=False,
                          shouldMerge=False),
                return_json=True))['feedbackResponses'][0]['isProcessed']

    def _toggle_history(self, page_url: str, contents_index: int) -> bool:
        if not self._logged_in:
            raise AuthenticationError('This method requires a call to '
                                      'login() first')
        content = self._download_page_soup(page_url)
        ytcfg = find_ytcfg(content)
        info = at_path(('contents.twoColumnBrowseResultsRenderer.'
                        'secondaryContents.browseFeedActionsRenderer.contents.'
                        f'{contents_index}.buttonRenderer.navigationEndpoint.'
                        'confirmDialogEndpoint.content.confirmDialogRenderer.'
                        'confirmEndpoint'), initial_data(content))
        return self._single_feedback_api_call(
            ytcfg, info['feedbackEndpoint']['feedbackToken'],
            info['clickTrackingParams'],
            info['commandMetadata']['webCommandMetadata']['apiUrl'])

    def toggle_search_history(self) -> bool:
        """Pauses or resumes search history depending on the current state."""
        return self._toggle_history(SEARCH_HISTORY_URL, 2)

    def toggle_watch_history(self) -> bool:
        """Pauses or resumes watch history depending on the current state."""
        return self._toggle_history(WATCH_HISTORY_URL, 3)

    def live_chat_history(
            self,
            only_first_page: bool = False) -> Iterator[LiveChatHistoryEntry]:
        """
        Fetches all live chat history.

        Fetches only the first page if ``only_first_page`` is ``True``.
        """
        if not self._logged_in:
            raise AuthenticationError('This method requires a call to '
                                      'login() first')
        content = self._download_page_soup(LIVE_CHAT_HISTORY_URL)
        ytcfg = find_ytcfg(content)
        headers = ytcfg_headers(ytcfg)
        headers['x-spf-previous'] = LIVE_CHAT_HISTORY_URL
        headers['x-spf-referer'] = LIVE_CHAT_HISTORY_URL
        item_section = at_path(
            ('contents.twoColumnBrowseResultsRenderer.tabs.0.'
             'tabRenderer.content.sectionListRenderer.contents.0.'
             'itemSectionRenderer'), initial_data(content))
        info = item_section['contents']
        for api_entry in (x['liveChatHistoryEntryRenderer'] for x in info):
            yield make_live_chat_history_entry(api_entry)
        if (only_first_page or 'continuations' not in item_section
                or not item_section['continuations']):
            return
        has_continuations = True
        while has_continuations:
            for cont in item_section['continuations']:
                data = cast(
                    Sequence[Any],
                    self._download_page(
                        BROWSE_AJAX_URL,
                        method='post',
                        params=dict(
                            ctoken=(
                                cont['nextContinuationData']['continuation']),
                            continuation=(
                                cont['nextContinuationData']['continuation']),
                            itct=(cont['nextContinuationData']
                                  ['clickTrackingParams'])),
                        data=dict(session_token=ytcfg['XSRF_TOKEN']),
                        headers=headers,
                        return_json=True))
                item_section = (data[1]['response']['continuationContents']
                                ['itemSectionContinuation'])
                for api_entry in (x['liveChatHistoryEntryRenderer']
                                  for x in item_section['contents']):
                    yield make_live_chat_history_entry(api_entry)
                has_continuations = ('continuations' in item_section
                                     and item_section['continuations'])

    def delete_live_chat_message(
            self,
            params: str,
            api_url: str = '/youtubei/v1/live_chat/delete_message',
            ytcfg: Optional[YtcfgDict] = None) -> Mapping[str, Any]:
        """
        Delete a live chat message by params value as given from
        ``live_chat_history()``.
        """
        if not self._logged_in:
            raise AuthenticationError('This method requires a call to '
                                      'login() first')
        if not ytcfg:
            content = self._download_page_soup(LIVE_CHAT_HISTORY_URL)
            ytcfg = find_ytcfg(content)
        return cast(
            Mapping[str, Any],
            self._download_page(
                f'https://www.youtube.com{api_url}',
                method='post',
                params=dict(key=ytcfg['INNERTUBE_API_KEY']),
                headers={
                    'Authority': 'www.youtube.com',
                    'Authorization': self._authorization_sapisidhash_header(),
                    'x-goog-authuser': '0',
                    'x-origin': 'https://www.youtube.com',
                },
                json=dict(
                    context=dict(
                        clickTracking=dict(clickTrackingParams=''),
                        client=context_client_body(ytcfg),
                        request=dict(consistencyTokenJars=[],
                                     internalExperimentFlags=[]),
                        user=dict(
                            onBehalfOfUser=ytcfg['DELEGATED_SESSION_ID'])),
                    params=params,
                ),
                return_json=True))

    def _comment_community_history(
            self,
            url: str,
            only_first_page: bool = False) -> Iterator[CommentHistoryEntry]:
        if not self._logged_in:
            raise AuthenticationError('This method requires a call to '
                                      'login() first')
        content = self._download_page_soup(url)
        ytcfg = find_ytcfg(content)
        headers = ytcfg_headers(ytcfg)
        headers['x-spf-previous'] = url
        headers['x-spf-referer'] = url
        item_section = at_path(
            ('contents.twoColumnBrowseResultsRenderer.tabs.'
             '0.tabRenderer.content.sectionListRenderer.contents.0.'
             'itemSectionRenderer'), initial_data(content))
        info = item_section['contents']
        if url == COMMENT_HISTORY_URL:
            delete_action_path = DEFAULT_DELETE_ACTION_PATH
        else:
            delete_action_path = (
                'actionMenu.menuRenderer.items.0.menuNavigationItemRenderer.'
                'navigationEndpoint.confirmDialogEndpoint.content.'
                'confirmDialogRenderer.confirmButton.buttonRenderer.'
                'serviceEndpoint.performCommentActionEndpoint.action')
        for api_entry in (x['commentHistoryEntryRenderer'] for x in info):
            yield make_comment_history_entry(api_entry, delete_action_path)
        if (only_first_page or 'continuations' not in item_section
                or not item_section['continuations']):
            return
        has_continuations = True
        while has_continuations:
            for cont in item_section['continuations']:
                data = cast(
                    Sequence[Any],
                    self._download_page(
                        BROWSE_AJAX_URL,
                        method='post',
                        params=dict(
                            ctoken=(
                                cont['nextContinuationData']['continuation']),
                            continuation=(
                                cont['nextContinuationData']['continuation']),
                            itct=(cont['nextContinuationData']
                                  ['clickTrackingParams'])),
                        data=dict(session_token=ytcfg['XSRF_TOKEN']),
                        headers=headers,
                        return_json=True))
                item_section = (data[1]['response']['continuationContents']
                                ['itemSectionContinuation'])
                for api_entry in (x['commentHistoryEntryRenderer']
                                  for x in item_section['contents']):
                    yield make_comment_history_entry(api_entry,
                                                     delete_action_path)
                has_continuations = ('continuations' in item_section
                                     and item_section['continuations'])

    def comment_history(
            self,
            only_first_page: bool = False) -> Iterator[CommentHistoryEntry]:
        yield from self._comment_community_history(COMMENT_HISTORY_URL,
                                                   only_first_page)

    def delete_comment(
            self,
            action: str,
            ytcfg: Optional[YtcfgDict] = None,
            api_url: str = '/youtubei/v1/comment/perform_comment_action'
    ) -> bool:
        if not self._logged_in:
            raise AuthenticationError('This method requires a call to '
                                      'login() first')
        if not ytcfg:
            content = self._download_page_soup(COMMENT_HISTORY_URL)
            ytcfg = find_ytcfg(content)
        return (at_path(
            'actions.0.removeCommentAction.actionResult.status',
            cast(
                Mapping[str, Any],
                self._download_page(
                    f'https://www.youtube.com{api_url}',
                    method='post',
                    params=dict(key=ytcfg['INNERTUBE_API_KEY']),
                    headers={
                        'Authority': 'www.youtube.com',
                        'Authorization':
                        self._authorization_sapisidhash_header(),
                        'x-goog-authuser': '0',
                        'x-origin': 'https://www.youtube.com',
                    },
                    json=dict(
                        actions=[action],
                        context=dict(
                            clickTracking=dict(clickTrackingParams=''),
                            client=context_client_body(ytcfg),
                            request=dict(consistencyTokenJars=[],
                                         internalExperimentFlags=[]),
                            user=dict(
                                onBehalfOfUser=ytcfg['DELEGATED_SESSION_ID'])),
                    ),
                    return_json=True))) == 'STATUS_SUCCEEDED')

    def update_comment(
            self,
            text: str,
            params: str,
            ytcfg: Optional[YtcfgDict] = None,
            api_url: str = '/youtubei/v1/comment/update_comment') -> bool:
        """
        Update a comment.

        The value for ``params`` is found on the video page where the comment
        is posted. It can be found by digging through ``ytInitialData``. This
        value must not be URL-encoded.
        """
        if not self._logged_in:
            raise AuthenticationError('This method requires a call to '
                                      'login() first')
        if not ytcfg:
            content = self._download_page_soup(COMMENT_HISTORY_URL)
            ytcfg = find_ytcfg(content)
        return (at_path(
            'actions.0.updateCommentAction.actionResult.status',
            cast(
                Mapping[str, Any],
                self._download_page(
                    f'https://www.youtube.com{api_url}',
                    method='post',
                    params=dict(key=ytcfg['INNERTUBE_API_KEY']),
                    headers={
                        'Authority': 'www.youtube.com',
                        'Authorization':
                        self._authorization_sapisidhash_header(),
                        'x-goog-authuser': '0',
                        'x-origin': 'https://www.youtube.com',
                    },
                    json=dict(
                        commentText=text,
                        context=dict(
                            clickTracking=dict(clickTrackingParams=''),
                            client=context_client_body(ytcfg),
                            request=dict(consistencyTokenJars=[],
                                         internalExperimentFlags=[]),
                            user=dict(
                                onBehalfOfUser=ytcfg['DELEGATED_SESSION_ID'])),
                        updateCommentParams=params,
                    ),
                    return_json=True))) == 'STATUS_SUCCEEDED')

    def community_history(
            self,
            only_first_page: bool = False) -> Iterator[CommentHistoryEntry]:
        yield from self._comment_community_history(COMMUNITY_HISTORY_URL,
                                                   only_first_page)

    def delete_community_entry(
            self,
            action: str,
            api_url: str = '/youtubei/v1/comment/perform_comment_action',
            ytcfg: Optional[YtcfgDict] = None) -> bool:
        if not self._logged_in:
            raise AuthenticationError('This method requires a call to '
                                      'login() first')
        if not ytcfg:
            content = self._download_page_soup(COMMENT_HISTORY_URL)
            ytcfg = find_ytcfg(content)
        return (at_path(
            'actionResults.0.status',
            cast(
                Mapping[str, Any],
                self._download_page(
                    f'https://www.youtube.com{api_url}',
                    method='post',
                    params=dict(key=ytcfg['INNERTUBE_API_KEY']),
                    headers={
                        'Authority': 'www.youtube.com',
                        'Authorization':
                        self._authorization_sapisidhash_header(),
                        'x-goog-authuser': '0',
                        'x-origin': 'https://www.youtube.com',
                    },
                    json=dict(
                        actions=[action],
                        context=dict(
                            clickTracking=dict(clickTrackingParams=''),
                            client=context_client_body(ytcfg),
                            request=dict(consistencyTokenJars=[],
                                         internalExperimentFlags=[]),
                            user=dict(
                                onBehalfOfUser=ytcfg['DELEGATED_SESSION_ID'])),
                    ),
                    return_json=True))) == 'STATUS_SUCCEEDED')

    def clear_search_history(self) -> bool:
        """Clear search history."""
        if not self._logged_in:
            raise AuthenticationError('This method requires a call to '
                                      'login() first')
        content = self._download_page_soup(SEARCH_HISTORY_URL)
        return self._single_feedback_api_call(
            find_ytcfg(content),
            at_path(
                'contents.twoColumnBrowseResultsRenderer.'
                'secondaryContents.browseFeedActionsRenderer.'
                'contents.1.buttonRenderer.navigationEndpoint.'
                'confirmDialogEndpoint.content.confirmDialogRenderer.'
                'confirmEndpoint.feedbackEndpoint.feedbackToken',
                initial_data(content)))
