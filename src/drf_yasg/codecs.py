from typing import Dict, List, Union, IO

from six import raise_from

import copy
import json
import logging
from collections import OrderedDict

from coreapi.compat import force_bytes
from ruamel import yaml

from six import binary_type, raise_from, text_type

from . import openapi
from .errors import SwaggerValidationError

logger = logging.getLogger(__name__)


def _validate_flex(spec):
    try:
        from flex.core import parse as validate_flex
        from flex.exceptions import ValidationError
    except ImportError:
        return

    try:
        validate_flex(spec)
    except ValidationError as ex:
        raise_from(SwaggerValidationError(str(ex)), ex)


def _validate_swagger_spec_validator(spec):
    from swagger_spec_validator.validator20 import validate_spec as validate_ssv
    from swagger_spec_validator.common import SwaggerValidationError as SSVErr

    try:
        validate_ssv(spec)
    except SSVErr as ex:
        raise_from(SwaggerValidationError(str(ex)), ex)


#:
VALIDATORS = {"flex": _validate_flex, "ssv": _validate_swagger_spec_validator}


class _OpenAPICodec:
    media_type = None

    def __init__(self, validators: List):
        self._validators = validators

    @property
    def validators(self):
        """List of validator names to apply"""
        return self._validators

    def encode(self, document: openapi.Swagger) -> bytes:
        """Transform an :class:`.Swagger` object to a sequence of bytes.

        Also performs validation and applies settings.

        :param openapi.Swagger document: Swagger spec object as generated by :class:`.OpenAPISchemaGenerator`
        :return: binary encoding of ``document``
        """
        if not isinstance(document, openapi.Swagger):
            raise TypeError("Expected a `openapi.Swagger` instance")

        spec = self.generate_swagger_object(document)
        errors = {}
        for validator in self.validators:
            try:
                # validate a deepcopy of the spec to prevent the validator from messing with it
                # for example, swagger_spec_validator adds an x-scope property to all references
                VALIDATORS[validator](copy.deepcopy(spec))
            except SwaggerValidationError as e:
                errors[validator] = str(e)

        if errors:
            exc = SwaggerValidationError(
                "spec validation failed: {}".format(errors), errors, spec, self
            )
            logger.warning(str(exc))
            raise exc

        return force_bytes(self._dump_dict(spec))

    def encode_error(self, err: Dict):
        """Dump an error message into an encoding-appropriate sequence of bytes"""
        return force_bytes(self._dump_dict(err))

    def _dump_dict(self, spec: Dict) -> str:
        """Dump the given dictionary into its string representation.

        :param dict spec: a python dict
        :return: string representation of ``spec``
        :rtype: str or bytes
        """
        raise NotImplementedError("override this method")

    def generate_swagger_object(self, swagger: openapi.Swagger) -> OrderedDict:
        """Generates the root Swagger object.

        :param openapi.Swagger swagger: Swagger spec object as generated by :class:`.OpenAPISchemaGenerator`
        :return: swagger spec as dict
        """
        return swagger.as_odict()


class OpenAPICodecJson(_OpenAPICodec):
    media_type = "application/json"

    def __init__(
        self,
        validators: List,
        pretty: bool = False,
        media_type: str = "application/json",
    ):
        super(OpenAPICodecJson, self).__init__(validators)
        self.pretty = pretty
        self.media_type = media_type

    def _dump_dict(self, spec: Dict):
        """Dump ``spec`` into JSON.

        :rtype: str"""
        if self.pretty:
            out = json.dumps(spec, indent=4, separators=(",", ": "), ensure_ascii=False)
            if out[-1] != "\n":
                out += "\n"
            return out
        else:
            return json.dumps(spec, ensure_ascii=False)


YAML_MAP_TAG = u"tag:yaml.org,2002:map"


class SaneYamlDumper(yaml.SafeDumper):
    """YamlDumper class usable for dumping ``OrderedDict`` and list instances in a standard way."""

    def ignore_aliases(self, data):
        """Disable YAML references."""
        return True

    def increase_indent(self, flow: bool = False, indentless: bool = False, **kwargs):
        """https://stackoverflow.com/a/39681672

        Indent list elements.
        """
        return super(SaneYamlDumper, self).increase_indent(
            flow=flow, indentless=False, **kwargs
        )

    def represent_odict(self, mapping, flow_style=None):  # pragma: no cover
        """https://gist.github.com/miracle2k/3184458

        Make PyYAML output an OrderedDict.

        It will do so fine if you use yaml.dump(), but that generates ugly, non-standard YAML code.

        To use yaml.safe_dump(), you need the following.
        """
        tag = YAML_MAP_TAG
        value = []
        node = yaml.MappingNode(tag, value, flow_style=flow_style)
        if self.alias_key is not None:
            self.represented_objects[self.alias_key] = node
        best_style = True
        if hasattr(mapping, "items"):
            mapping = mapping.items()
        for item_key, item_value in mapping:
            node_key = self.represent_data(item_key)
            node_value = self.represent_data(item_value)
            if not (isinstance(node_key, yaml.ScalarNode) and not node_key.style):
                best_style = False
            if not (isinstance(node_value, yaml.ScalarNode) and not node_value.style):
                best_style = False
            value.append((node_key, node_value))
        if flow_style is None:
            if self.default_flow_style is not None:
                node.flow_style = self.default_flow_style
            else:
                node.flow_style = best_style
        return node

    def represent_text(self, text):
        if "\n" in text:
            return self.represent_scalar('tag:yaml.org,2002:str', text, style='|')
        return self.represent_scalar('tag:yaml.org,2002:str', text)


SaneYamlDumper.add_representer(binary_type, SaneYamlDumper.represent_text)
SaneYamlDumper.add_representer(text_type, SaneYamlDumper.represent_text)
SaneYamlDumper.add_representer(OrderedDict, SaneYamlDumper.represent_odict)
SaneYamlDumper.add_multi_representer(OrderedDict, SaneYamlDumper.represent_odict)


def yaml_sane_dump(data: Dict, binary: bool) -> Union[str, bytes]:
    """Dump the given data dictionary into a sane format:

        * OrderedDicts are dumped as regular mappings instead of non-standard !!odict
        * multi-line mapping style instead of json-like inline style
        * list elements are indented into their parents
        * YAML references/aliases are disabled

    :param dict data: the data to be dumped
    :param bool binary: True to return a utf-8 encoded binary object, False to return a string
    :return: the serialized YAML
    """
    return yaml.dump(
        data,
        Dumper=SaneYamlDumper,
        allow_unicode=True,
        default_flow_style=False,
        encoding="utf-8" if binary else None,
    )


class SaneYamlLoader(yaml.SafeLoader):
    def construct_odict(self, node, deep=False):
        self.flatten_mapping(node)
        return OrderedDict(self.construct_pairs(node))


SaneYamlLoader.add_constructor(YAML_MAP_TAG, SaneYamlLoader.construct_odict)


def yaml_sane_load(stream: Union[str, IO]):
    """Load the given YAML stream while preserving the input order for mapping items.

    :param stream: YAML stream (can be a string or a file-like object)
    :rtype: OrderedDict
    """
    return yaml.load(stream, Loader=SaneYamlLoader)


class OpenAPICodecYaml(_OpenAPICodec):
    media_type = "application/yaml"

    def __init__(self, validators: List, media_type: str = "application/yaml"):
        super(OpenAPICodecYaml, self).__init__(validators)
        self.media_type = media_type

    def _dump_dict(self, spec: Dict) -> bytes:
        """Dump ``spec`` into YAML. """
        return yaml_sane_dump(spec, binary=True)
