from typing import Set, Dict, Any, Optional, Union, FrozenSet
from pathlib import Path
from copy import deepcopy
import platform
import pprint
import reprlib
import logging

import networkx as nx
import tomli

from .data_model import TopLevelWokeConfig, CompilerWokeConfig
from .utils import change_cwd


class UnsupportedPlatformError(Exception):
    """
    The current platform is not supported. Supported platforms are: Linux, macOS, Windows.
    """


class WokeConfig:
    __woke_root_path: Path
    __project_root_path: Path
    __loaded_files: Set[Path]
    __config_raw: Dict[str, Any]
    __config: TopLevelWokeConfig

    def __init__(
        self,
        *_,
        project_root_path: Optional[Union[str, Path]] = None,
        woke_root_path: Optional[Union[str, Path]] = None,
    ):
        if woke_root_path is None:
            system = platform.system()
            if system == "Linux":
                self.__woke_root_path = Path.home() / ".woke"
            elif system == "Darwin":
                self.__woke_root_path = Path.home() / ".woke"
            elif system == "Windows":
                self.__woke_root_path = Path.home() / "Woke"
            else:
                raise UnsupportedPlatformError(f"Platform `{system}` is not supported.")
        else:
            self.__woke_root_path = Path(woke_root_path)

        if project_root_path is None:
            self.__project_root_path = Path.cwd().resolve()
        else:
            self.__project_root_path = Path(project_root_path).resolve()

        if not self.__project_root_path.is_dir():
            raise ValueError(
                f"Project root path `{self.__project_root_path}` is not a directory."
            )

        # make sure that Woke root path exists
        self.__woke_root_path.mkdir(parents=True, exist_ok=True)
        self.__woke_root_path = self.__woke_root_path.resolve(strict=True)

        self.__loaded_files = set()
        self.__config_raw = dict()
        self.__config = TopLevelWokeConfig()

    def __str__(self) -> str:
        return pprint.pformat(self.__config_raw, indent=4)

    def __repr__(self) -> str:
        config_dict = reprlib.repr(self.__config_raw)
        return f"{self.__class__.__name__}.fromdict({config_dict}, project_root_path={repr(self.__project_root_path)}, woke_root_path={repr(self.__woke_root_path)})"

    def __merge_dicts(self, old: Dict[str, Any], new: Dict[str, Any]) -> None:
        for k, v in new.items():
            if k not in old.keys():
                old[k] = v
            else:
                if isinstance(v, dict):
                    self.__merge_dicts(old[k], new[k])
                else:
                    old[k] = v

    def __load_file(
        self,
        parent: Optional[Path],
        path: Path,
        new_config: Dict[str, Any],
        graph: nx.DiGraph,
    ) -> None:
        if not path.is_file():
            if parent is None:
                logging.warning(f"Config file `{path}` does not exist.")
            else:
                logging.warning(
                    f"Config file `{path}` loaded from `{parent}` does not exist."
                )
        else:
            # change the current working dir so that we can resolve relative paths
            with change_cwd(path.parent):
                with path.open("rb") as f:
                    loaded_config = tomli.load(f)

                graph.add_node(path, config=loaded_config)
                if parent is not None:
                    graph.add_edge(parent, path)

                # detect cyclic subconfigs
                if not nx.is_directed_acyclic_graph(graph):
                    cycles = list(nx.simple_cycles(graph))
                    error = f"Found cyclic config subconfigs:"
                    for no, cycle in enumerate(cycles):
                        error += f"\nCycle {no}:\n"
                        for path in cycle:
                            error += f"{path}\n"
                    raise ValueError(error)

                # validate the loaded config
                parsed_config = TopLevelWokeConfig.parse_obj(loaded_config)

                # rebuild the loaded config from the pydantic model
                # this ensures that all stored paths are absolute
                loaded_config = parsed_config.dict(by_alias=True, exclude_unset=True)

                # merge the original config and the newly loaded config
                self.__merge_dicts(new_config, loaded_config)

                for subconfig_path in parsed_config.subconfigs:
                    self.__load_file(path, subconfig_path, new_config, graph)

    @classmethod
    def fromdict(
        cls,
        config_dict: Dict[str, Any],
        *_,
        project_root_path: Optional[Union[str, Path]] = None,
        woke_root_path: Optional[Union[str, Path]] = None,
    ) -> "WokeConfig":
        """
        Build `WokeConfig` class from a dictionary and optional Woke root and project root paths.
        """
        instance = cls(
            project_root_path=project_root_path, woke_root_path=woke_root_path
        )
        config = TopLevelWokeConfig.parse_obj(config_dict)
        instance.__config_raw = deepcopy(config_dict)
        instance.__config = config
        return instance

    def load_configs(self) -> None:
        """
        Load both the global config file `config.toml` located in the Woke root directory
        and the project specific config file `woke.toml` located in the project root directory.
        This is expected to be called right after `WokeConfig` instantiation.
        """
        self.load(self.woke_root_path / "config.toml")
        self.load(self.project_root_path / "woke.toml")

    def load(self, path: Path) -> None:
        """
        Load config from the provided file path. Any already loaded config options are overridden by the options loaded
        from this file.
        """
        subconfigs_graph = nx.DiGraph()
        config_raw_copy = deepcopy(self.__config_raw)

        self.__load_file(None, path.resolve(), config_raw_copy, subconfigs_graph)

        config = TopLevelWokeConfig.parse_obj(config_raw_copy)
        self.__config_raw = config_raw_copy
        self.__config = config
        self.__loaded_files.update(subconfigs_graph.nodes)

    @property
    def loaded_files(self) -> FrozenSet[Path]:
        """
        Return frozenset of all loaded config file paths, including files that were loaded using the `subconfigs` config key.
        """
        return frozenset(self.__loaded_files)

    @property
    def woke_root_path(self) -> Path:
        """
        Return the system path to the Woke root directory.
        """
        return self.__woke_root_path

    @property
    def project_root_path(self) -> Path:
        """
        Return the system path of the currently open project.
        """
        return self.__project_root_path

    @property
    def compiler(self) -> CompilerWokeConfig:
        """
        Return compiler-specific config options.
        """
        return self.__config.compiler
