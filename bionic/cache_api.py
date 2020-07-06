"""
Contains the Cache class, which provides a user-facing API for directly interacting
with Bionic's cache.
"""

from functools import total_ordering

from .descriptors.parsing import entity_dnode_from_descriptor
from .persistence import path_from_url, is_file_url


class Cache:
    """
    A programmatic interface to Bionic's persistent cache.

    Accessible as an attribute named ``cache`` on a :class:`Flow <bionic.Flow>`
    object. Use ``get_entries`` to iterate through the set of cache entries.
    """

    def __init__(self, deriver):
        # Currently we don't really need the entire deriver, just the persistent cache.
        # However, in the future I expect we'll use the deriver to figure out which
        # artifacts are relevant to the current flow, and in the meantime it's
        # convenient to have access to it so that we can call get_ready() at the last
        # minute.
        self._deriver = deriver

    def get_entries(self):
        """
        Returns a sequence of :class:`CacheEntry <bionic.cache_api.CacheEntry>`
        objects, one for each artifact in Bionic's persistent cache.

        Cached artifacts are stored by flow name, so this will include any artifacts
        generated by a flow with the same name as this one; this typically includes
        the current Flow object, as well as any older or modified versions.

        Artifacts are returned for all cache tiers that are enabled for the current
        flow. For example, if GCS caching is enabled, this method will return
        entities from both the "local" (on-disk") and "cloud" (GCS) tiers.
        """

        # These private accesses are a bit gross, but I'm not sure it's worth adding
        # more layers of APIs to avoid them.
        self._deriver.get_ready()
        persistent_cache = self._deriver._bootstrap.persistent_cache

        stores = [
            store
            for store in [persistent_cache._local_store, persistent_cache._cloud_store]
            if store is not None
        ]

        return (
            CacheEntry(cache=self, inv_item=inv_item,)
            for store in stores
            for inv_item in store.inventory.list_items()
        )


def path_from_url_if_file(url):
    """Converts a URL into a file path if it is a file URL; otherwise returns None."""

    if is_file_url(url):
        return path_from_url(url)
    else:
        return None


@total_ordering
class CacheEntry:
    """
    Represents an artifact in Bionic's persistent cache.

    Has the following fields:

    - ``tier``: "local" or "cloud", depending on which tier of the cache the artifact
      is in.
    - ``entity``: the name of the cached entity, or ``None`` if the artifact is does
      not correspond to an entity
    - ``artifact_url``: a URL to the cached artifact file or blob
    - ``metadata_url``: a URL to the metadata file or blob describing the artifact
    - ``artifact_path``: a Path object locating the artifact file (if it's a local
      file) or None (if it's a cloud blob)
    - ``metadata_path``: a Path object locating the metadata file (if it's a local
      file) or None (if it's a cloud blob)
    """

    def __init__(self, cache, inv_item):
        self._cache = cache
        self._comparison_key = inv_item.abs_metadata_url

        self.tier = inv_item.inventory.tier
        self.artifact_url = inv_item.abs_artifact_url
        self.metadata_url = inv_item.abs_metadata_url
        self._descriptor = inv_item.descriptor
        self._inventory = inv_item.inventory

    @property
    def entity(self):
        try:
            return entity_dnode_from_descriptor(self._descriptor).to_entity_name()
        except ValueError:
            return None

    @property
    def artifact_path(self):
        return path_from_url_if_file(self.artifact_url)

    @property
    def metadata_path(self):
        return path_from_url_if_file(self.metadata_url)

    def __hash__(self):
        return hash(self._comparison_key)

    def __eq__(self, other):
        if not isinstance(other, CacheEntry):
            return False
        return self._comparison_key == other._comparison_key

    def __lt__(self, other):
        if not isinstance(other, CacheEntry):
            return TypeError(f"Can't compare {self!r} with non-CacheEntry {other!r}")
        return self._comparison_key < other._comparison_key

    def __repr__(self):
        return f"CacheEntry(metadata_url={self.metadata_url!r})"
