"""Tap abstract class."""

import abc
import json
from pathlib import PurePath

import singer
from singer_sdk.helpers import classproperty
from typing import Any, List, Optional, Dict, Union

import click
from singer.catalog import Catalog

from singer_sdk.plugin_base import PluginBase
from singer_sdk.streams.core import Stream


class Tap(PluginBase, metaclass=abc.ABCMeta):
    """Abstract base class for taps."""

    # Constructor

    def __init__(
        self,
        config: Union[PurePath, str, dict, None] = None,
        catalog: Union[PurePath, str, dict, None] = None,
        state: Union[PurePath, str, dict, None] = None,
    ) -> None:
        """Initialize the tap."""
        if isinstance(state, dict):
            state_dict = state
        else:
            state_dict = self.read_optional_json_file(state) or {}
        self._input_catalog: Optional[dict] = None
        if isinstance(catalog, dict):
            self._input_catalog = catalog
        elif catalog is not None:
            self._input_catalog = self.read_optional_json_file(catalog)
        self._state = state_dict or {}
        self._streams: Optional[Dict[str, Stream]] = None
        super().__init__(config=config)

    # Class properties

    @property
    def streams(self) -> Dict[str, Stream]:
        """Return a list of streams, using discovery or a provided catalog.

        Results will be cached after first execution.
        """
        if self._streams is None:
            self._streams = {}
            for stream in self.load_streams():
                if self.input_catalog:
                    stream.apply_catalog(self.input_catalog)
                self._streams[stream.name] = stream
        return self._streams

    @property
    def state(self) -> dict:
        """Return a state dict."""
        return self._state

    @property
    def input_catalog(self) -> Optional[dict]:
        """Return the catalog dictionary input, or None if not provided."""
        return self._input_catalog

    @property
    def capabilities(self) -> List[str]:
        """Return a list of supported capabilities."""
        return ["sync", "catalog", "state", "discover"]

    # Stream detection:

    def run_discovery(self) -> str:
        """Write the catalog json to STDOUT and return the same as a string."""
        catalog_json = self.get_catalog_json()
        print(catalog_json)
        return catalog_json

    def get_singer_catalog(self) -> Catalog:
        """Return a Catalog object."""
        catalog_entries = [
            stream.singer_catalog_entry for stream in self.streams.values()
        ]
        return Catalog(catalog_entries)

    def get_catalog_json(self) -> str:
        """Return the tap's catalog as formatted json text."""
        return json.dumps(self.get_singer_catalog().to_dict(), indent=2)

    def discover_streams(self) -> List[Stream]:
        """Return a list of discovered streams."""
        raise NotImplementedError(
            f"Tap '{self.name}' does not support discovery. "
            "Please set the '--catalog' command line argument and try again."
        )

    def load_streams(self) -> List[Stream]:
        """Load streams from discovery or input catalog.

        - Implementations may reference `self.discover_streams()`, `self.input_catalog`,
          or both.
        - By default, return the output of `self.discover_streams()` to enumerate
          discovered streams.
        - Developers may override this method if discovery is not supported, or if
          discovery should not be run by default.
        """
        return self.discover_streams()

    # Bookmarks and state management

    def load_state(self, state: Dict[str, Any]) -> Dict[str, Any]:
        """Return a properly initalized state given an arbitrary dict input.

        Override this method to perform validation and backwards compatibility updates.
        """
        return state

    def merge_bookmarks(self, stream: Stream, new_bookmarks: Dict[str, Any]) -> None:
        """Apply the provided dictionary of new bookmark values."""
        for k, v in new_bookmarks.items():
            self._state = singer.write_bookmark(self._state, stream.tap_stream_id, k, v)

    def update_bookmarks(self, stream: Stream, latest_record: Dict[str, Any]):
        """Update the stream's internal state with data from the provided record."""
        if not self._state:
            self.merge_bookmarks(
                stream, {"version": stream.get_stream_version()},
            )
        if latest_record:
            if stream.replication_method == "FULL_TABLE":
                max_pk_values = singer._get_bookmark("max_pk_values")
                if max_pk_values:
                    self.merge_bookmarks(
                        stream,
                        {
                            "last_pk_fetched": {
                                k: v
                                for k, v in latest_record.items()
                                if k in (stream.primary_keys or [])
                            }
                        },
                    )
            elif stream.replication_method in ["INCREMENTAL", "LOG_BASED"]:
                if stream.replication_key is not None:
                    self.merge_bookmarks(
                        stream,
                        {
                            "replication_key": stream.replication_key,
                            "replication_key_value": latest_record[
                                stream.replication_key
                            ],
                        },
                    )

    # Sync methods

    def sync_one(self, stream_name: str):
        """Sync a single stream."""
        if stream_name not in self.streams:
            raise ValueError(
                f"Could not find stream '{stream_name}' in streams list: "
                f"{sorted(self.streams.keys())}"
            )
        stream = self.streams[stream_name]
        stream.sync()

    def sync_all(self):
        """Sync all streams."""
        for stream in self.streams.values():
            stream.sync()

    # Command Line Execution

    @classproperty
    def cli(cls):
        """Execute standard CLI handler for taps."""

        @click.option("--version", is_flag=True)
        @click.option("--about", is_flag=True)
        @click.option("--discover", is_flag=True)
        @click.option("--format")
        @click.option("--config")
        @click.option("--catalog")
        @click.command()
        def cli(
            version: bool = False,
            about: bool = False,
            discover: bool = False,
            config: str = None,
            state: str = None,
            catalog: str = None,
            format: str = None,
        ):
            """Handle command line execution."""
            if version:
                cls.print_version()
                return
            if about:
                cls.print_about(format)
                return
            tap = cls(config=config, state=state, catalog=catalog)
            if discover:
                tap.run_discovery()
            else:
                tap.sync_all()

        return cli


cli = Tap.cli