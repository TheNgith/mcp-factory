# Generated MCP server — zstd
# Run:  pip install -r requirements.txt  &&  cp .env.example .env  (fill values)
#       python server.py

import os
import json
import ctypes
import subprocess
import threading
from pathlib import Path
from flask import Flask, request, jsonify, send_from_directory
from openai import OpenAI
from dotenv import load_dotenv

# Always load .env from the server's own directory, regardless of cwd
load_dotenv(dotenv_path=Path(__file__).parent / ".env")
# openai 2.x reads OPENAI_BASE_URL directly from os.environ;
# if it's set to "" (e.g. by a system env var) it overrides base_url=None.
# Remove it entirely so the library defaults to api.openai.com.
if not os.environ.get("OPENAI_BASE_URL"):
    os.environ.pop("OPENAI_BASE_URL", None)

app = Flask(__name__, static_folder="static")

# ── Tool registry (injected by generator) ──────────────────────────────────
INVOCABLES = json.loads(r"""[
  {
    "name": "ZDICT_addEntropyTablesFromBuffer",
    "tool_id": "dll_ZDICT_addEntropyTablesFromBuffer_1",
    "kind": "export",
    "ordinal": 1,
    "rva": "00077410",
    "confidence": "guaranteed",
    "confidence_factors": {
      "has_signature": true,
      "has_documentation": false,
      "has_parameters": true,
      "has_return_type": true,
      "is_forwarded": true,
      "is_ordinal_only": false
    },
    "signature": {
      "return_type": "size_t",
      "parameters": "void* dictBuffer, size_t dictContentSize, size_t dictBufferCapacity, const void* samplesBuffer, const size_t* samplesSizes, unsigned nbSamples",
      "calling_convention": null,
      "full_prototype": "size_t ZDICT_addEntropyTablesFromBuffer(void* dictBuffer, size_t dictContentSize, size_t dictBufferCapacity, const void* samplesBuffer, const size_t* samplesSizes, unsigned nbSamples)"
    },
    "documentation": {
      "summary": "",
      "description": "",
      "source_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zdict.h",
      "source_line": 474
    },
    "evidence": {
      "discovered_by": "export_analyzer",
      "header_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zdict.h",
      "forwarded_to": null,
      "demangled_name": null
    },
    "mcp": {
      "version": "1.0",
      "input_schema": {
        "type": "object",
        "properties": {
          "dictBuffer": {
            "type": "string",
            "description": "void*"
          },
          "dictContentSize": {
            "type": "integer",
            "description": "size_t"
          },
          "dictBufferCapacity": {
            "type": "integer",
            "description": "size_t"
          },
          "samplesBuffer": {
            "type": "string",
            "description": "void*"
          },
          "samplesSizes": {
            "type": "string",
            "description": "size_t*"
          },
          "nbSamples": {
            "type": "integer",
            "description": "unsigned"
          }
        },
        "required": [
          "dictBuffer",
          "dictContentSize",
          "dictBufferCapacity",
          "samplesBuffer",
          "samplesSizes",
          "nbSamples"
        ]
      },
      "execution": {
        "method": "dll_import",
        "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
        "function_name": "ZDICT_addEntropyTablesFromBuffer",
        "calling_convention": "stdcall",
        "charset": "ansi"
      }
    },
    "metadata": {
      "is_signed": false,
      "publisher": null
    },
    "execution": {
      "method": "dll_import",
      "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
      "function_name": "ZDICT_addEntropyTablesFromBuffer",
      "calling_convention": "stdcall",
      "charset": "ansi"
    },
    "return_type": "size_t",
    "parameters": [
      {
        "name": "dictBuffer",
        "type": "void*",
        "required": true,
        "description": "void*"
      },
      {
        "name": "dictContentSize",
        "type": "size_t",
        "required": true,
        "description": "size_t"
      },
      {
        "name": "dictBufferCapacity",
        "type": "size_t",
        "required": true,
        "description": "size_t"
      },
      {
        "name": "samplesBuffer",
        "type": "const void*",
        "required": true,
        "description": "const void*"
      },
      {
        "name": "samplesSizes",
        "type": "const size_t*",
        "required": true,
        "description": "const size_t*"
      },
      {
        "name": "nbSamples",
        "type": "unsigned",
        "required": true,
        "description": "unsigned"
      }
    ]
  },
  {
    "name": "ZDICT_finalizeDictionary",
    "tool_id": "dll_ZDICT_finalizeDictionary_2",
    "kind": "export",
    "ordinal": 2,
    "rva": "00078A70",
    "confidence": "guaranteed",
    "confidence_factors": {
      "has_signature": true,
      "has_documentation": true,
      "has_parameters": true,
      "has_return_type": true,
      "is_forwarded": true,
      "is_ordinal_only": false
    },
    "signature": {
      "return_type": "size_t",
      "parameters": "void* dstDictBuffer, size_t maxDictSize, const void* dictContent, size_t dictContentSize, const void* samplesBuffer, const size_t* samplesSizes, unsigned nbSamples, ZDICT_params_t parameters",
      "calling_convention": null,
      "full_prototype": "ZDICTLIB_API size_t ZDICT_finalizeDictionary(void* dstDictBuffer, size_t maxDictSize, const void* dictContent, size_t dictContentSize, const void* samplesBuffer, const size_t* samplesSizes, unsigned nbSamples, ZDICT_params_t parameters)"
    },
    "documentation": {
      "summary": "! ZDICT_finalizeDictionary():\nGiven a custom content as a basis for dictionary, and a set of samples,\nfinalize dictionary by adding headers and statistics according to the zstd\ndictionary format.\n\nSamples must be stored concatenated in a flat buffer `samplesBuffer`,\nsupplied with an array of sizes `samplesSizes`, providing the size of each\nsample in order. The samples are used to construct the statistics, so they\nshould be representative of what you will compress with this dictionary.\n\nThe compression level can be set in `parameters`. You should pass the\ncompression level you expect to use in production. The statistics for each\ncompression level differ, so tuning the dictionary for the compression level\ncan help quite a bit.\n\nYou can set an explicit dictionary ID in `parameters`, or allow us to pick\na random dictionary ID for you, but we can't guarantee no collisions.\n\nThe dstDictBuffer and the dictContent may overlap, and the content will be\nappended to the end of the header. If the header + the content doesn't fit in\nmaxDictSize the beginning of the content is truncated to make room, since it\nis presumed that the most profitable content is at the end of the dictionary,\nsince that is the cheapest to reference.\n\n`maxDictSize` must be >= max(dictContentSize, ZDICT_DICTSIZE_MIN).\n\n@return: size of dictionary stored into `dstDictBuffer` (<= `maxDictSize`),\n         or an error code, which can be tested by ZDICT_isError().\nNote: ZDICT_finalizeDictionary() will push notifications into stderr if\n      instructed to, using notificationLevel>0.\nNOTE: This function currently may fail in several edge cases including:\n        * Not enough samples\n        * Samples are uncompressible\n        * Samples are all exactly the same",
      "description": "! ZDICT_finalizeDictionary():\nGiven a custom content as a basis for dictionary, and a set of samples,\nfinalize dictionary by adding headers and statistics according to the zstd\ndictionary format.\n\nSamples must be stored concatenated in a flat buffer `samplesBuffer`,\nsupplied with an array of sizes `samplesSizes`, providing the size of each\nsample in order. The samples are used to construct the statistics, so they\nshould be representative of what you will compress with this dictionary.\n\nThe compression level can be set in `parameters`. You should pass the\ncompression level you expect to use in production. The statistics for each\ncompression level differ, so tuning the dictionary for the compression level\ncan help quite a bit.\n\nYou can set an explicit dictionary ID in `parameters`, or allow us to pick\na random dictionary ID for you, but we can't guarantee no collisions.\n\nThe dstDictBuffer and the dictContent may overlap, and the content will be\nappended to the end of the header. If the header + the content doesn't fit in\nmaxDictSize the beginning of the content is truncated to make room, since it\nis presumed that the most profitable content is at the end of the dictionary,\nsince that is the cheapest to reference.\n\n`maxDictSize` must be >= max(dictContentSize, ZDICT_DICTSIZE_MIN).\n\n@return: size of dictionary stored into `dstDictBuffer` (<= `maxDictSize`),\n         or an error code, which can be tested by ZDICT_isError().\nNote: ZDICT_finalizeDictionary() will push notifications into stderr if\n      instructed to, using notificationLevel>0.\nNOTE: This function currently may fail in several edge cases including:\n        * Not enough samples\n        * Samples are uncompressible\n        * Samples are all exactly the same",
      "source_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zdict.h",
      "source_line": 262
    },
    "evidence": {
      "discovered_by": "export_analyzer",
      "header_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zdict.h",
      "forwarded_to": null,
      "demangled_name": null
    },
    "mcp": {
      "version": "1.0",
      "input_schema": {
        "type": "object",
        "properties": {
          "dstDictBuffer": {
            "type": "string",
            "description": "void*"
          },
          "maxDictSize": {
            "type": "integer",
            "description": "size_t"
          },
          "dictContent": {
            "type": "string",
            "description": "void*"
          },
          "dictContentSize": {
            "type": "integer",
            "description": "size_t"
          },
          "samplesBuffer": {
            "type": "string",
            "description": "void*"
          },
          "samplesSizes": {
            "type": "string",
            "description": "size_t*"
          },
          "nbSamples": {
            "type": "integer",
            "description": "unsigned"
          },
          "parameters": {
            "type": "string",
            "description": "ZDICT_params_t"
          }
        },
        "required": [
          "dstDictBuffer",
          "maxDictSize",
          "dictContent",
          "dictContentSize",
          "samplesBuffer",
          "samplesSizes",
          "nbSamples",
          "parameters"
        ]
      },
      "execution": {
        "method": "dll_import",
        "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
        "function_name": "ZDICT_finalizeDictionary",
        "calling_convention": "stdcall",
        "charset": "ansi"
      }
    },
    "metadata": {
      "is_signed": false,
      "publisher": null
    },
    "execution": {
      "method": "dll_import",
      "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
      "function_name": "ZDICT_finalizeDictionary",
      "calling_convention": "stdcall",
      "charset": "ansi"
    },
    "return_type": "size_t",
    "parameters": [
      {
        "name": "dstDictBuffer",
        "type": "void*",
        "required": true,
        "description": "void*"
      },
      {
        "name": "maxDictSize",
        "type": "size_t",
        "required": true,
        "description": "size_t"
      },
      {
        "name": "dictContent",
        "type": "const void*",
        "required": true,
        "description": "const void*"
      },
      {
        "name": "dictContentSize",
        "type": "size_t",
        "required": true,
        "description": "size_t"
      },
      {
        "name": "samplesBuffer",
        "type": "const void*",
        "required": true,
        "description": "const void*"
      },
      {
        "name": "samplesSizes",
        "type": "const size_t*",
        "required": true,
        "description": "const size_t*"
      },
      {
        "name": "nbSamples",
        "type": "unsigned",
        "required": true,
        "description": "unsigned"
      },
      {
        "name": "parameters",
        "type": "ZDICT_params_t",
        "required": true,
        "description": "ZDICT_params_t"
      }
    ]
  },
  {
    "name": "ZDICT_getDictHeaderSize",
    "tool_id": "dll_ZDICT_getDictHeaderSize_3",
    "kind": "export",
    "ordinal": 3,
    "rva": "00078C70",
    "confidence": "guaranteed",
    "confidence_factors": {
      "has_signature": true,
      "has_documentation": true,
      "has_parameters": true,
      "has_return_type": true,
      "is_forwarded": true,
      "is_ordinal_only": false
    },
    "signature": {
      "return_type": "size_t",
      "parameters": "const void* dictBuffer, size_t dictSize",
      "calling_convention": null,
      "full_prototype": "ZDICTLIB_API size_t ZDICT_getDictHeaderSize(const void* dictBuffer, size_t dictSize)"
    },
    "documentation": {
      "summary": "returns dict header size; returns a ZSTD error code on failure",
      "description": "returns dict header size; returns a ZSTD error code on failure",
      "source_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zdict.h",
      "source_line": 270
    },
    "evidence": {
      "discovered_by": "export_analyzer",
      "header_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zdict.h",
      "forwarded_to": null,
      "demangled_name": null
    },
    "mcp": {
      "version": "1.0",
      "input_schema": {
        "type": "object",
        "properties": {
          "dictBuffer": {
            "type": "string",
            "description": "void*"
          },
          "dictSize": {
            "type": "integer",
            "description": "size_t"
          }
        },
        "required": [
          "dictBuffer",
          "dictSize"
        ]
      },
      "execution": {
        "method": "dll_import",
        "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
        "function_name": "ZDICT_getDictHeaderSize",
        "calling_convention": "stdcall",
        "charset": "ansi"
      }
    },
    "metadata": {
      "is_signed": false,
      "publisher": null
    },
    "execution": {
      "method": "dll_import",
      "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
      "function_name": "ZDICT_getDictHeaderSize",
      "calling_convention": "stdcall",
      "charset": "ansi"
    },
    "return_type": "size_t",
    "parameters": [
      {
        "name": "dictBuffer",
        "type": "const void*",
        "required": true,
        "description": "const void*"
      },
      {
        "name": "dictSize",
        "type": "size_t",
        "required": true,
        "description": "size_t"
      }
    ]
  },
  {
    "name": "ZDICT_getDictID",
    "tool_id": "dll_ZDICT_getDictID_4",
    "kind": "export",
    "ordinal": 4,
    "rva": "000685E0",
    "confidence": "guaranteed",
    "confidence_factors": {
      "has_signature": true,
      "has_documentation": true,
      "has_parameters": true,
      "has_return_type": true,
      "is_forwarded": true,
      "is_ordinal_only": false
    },
    "signature": {
      "return_type": "unsigned",
      "parameters": "const void* dictBuffer, size_t dictSize",
      "calling_convention": null,
      "full_prototype": "ZDICTLIB_API unsigned ZDICT_getDictID(const void* dictBuffer, size_t dictSize)"
    },
    "documentation": {
      "summary": "*< extracts dictID; @return zero if error (not a valid dictionary)",
      "description": "*< extracts dictID; @return zero if error (not a valid dictionary)",
      "source_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zdict.h",
      "source_line": 269
    },
    "evidence": {
      "discovered_by": "export_analyzer",
      "header_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zdict.h",
      "forwarded_to": null,
      "demangled_name": null
    },
    "mcp": {
      "version": "1.0",
      "input_schema": {
        "type": "object",
        "properties": {
          "dictBuffer": {
            "type": "string",
            "description": "void*"
          },
          "dictSize": {
            "type": "integer",
            "description": "size_t"
          }
        },
        "required": [
          "dictBuffer",
          "dictSize"
        ]
      },
      "execution": {
        "method": "dll_import",
        "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
        "function_name": "ZDICT_getDictID",
        "calling_convention": "stdcall",
        "charset": "ansi"
      }
    },
    "metadata": {
      "is_signed": false,
      "publisher": null
    },
    "execution": {
      "method": "dll_import",
      "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
      "function_name": "ZDICT_getDictID",
      "calling_convention": "stdcall",
      "charset": "ansi"
    },
    "return_type": "unsigned",
    "parameters": [
      {
        "name": "dictBuffer",
        "type": "const void*",
        "required": true,
        "description": "const void*"
      },
      {
        "name": "dictSize",
        "type": "size_t",
        "required": true,
        "description": "size_t"
      }
    ]
  },
  {
    "name": "ZDICT_getErrorName",
    "tool_id": "dll_ZDICT_getErrorName_5",
    "kind": "export",
    "ordinal": 5,
    "rva": "000035E0",
    "confidence": "guaranteed",
    "confidence_factors": {
      "has_signature": true,
      "has_documentation": false,
      "has_parameters": true,
      "has_return_type": true,
      "is_forwarded": true,
      "is_ordinal_only": false
    },
    "signature": {
      "return_type": "const char*",
      "parameters": "size_t errorCode",
      "calling_convention": null,
      "full_prototype": "ZDICTLIB_API const char* ZDICT_getErrorName(size_t errorCode)"
    },
    "documentation": {
      "summary": "",
      "description": "",
      "source_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zdict.h",
      "source_line": 272
    },
    "evidence": {
      "discovered_by": "export_analyzer",
      "header_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zdict.h",
      "forwarded_to": null,
      "demangled_name": null
    },
    "mcp": {
      "version": "1.0",
      "input_schema": {
        "type": "object",
        "properties": {
          "errorCode": {
            "type": "integer",
            "description": "size_t"
          }
        },
        "required": [
          "errorCode"
        ]
      },
      "execution": {
        "method": "dll_import",
        "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
        "function_name": "ZDICT_getErrorName",
        "calling_convention": "stdcall",
        "charset": "ansi"
      }
    },
    "metadata": {
      "is_signed": false,
      "publisher": null
    },
    "execution": {
      "method": "dll_import",
      "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
      "function_name": "ZDICT_getErrorName",
      "calling_convention": "stdcall",
      "charset": "ansi"
    },
    "return_type": "const char*",
    "parameters": [
      {
        "name": "errorCode",
        "type": "size_t",
        "required": true,
        "description": "size_t"
      }
    ]
  },
  {
    "name": "ZDICT_isError",
    "tool_id": "dll_ZDICT_isError_6",
    "kind": "export",
    "ordinal": 6,
    "rva": "00003610",
    "confidence": "guaranteed",
    "confidence_factors": {
      "has_signature": true,
      "has_documentation": false,
      "has_parameters": true,
      "has_return_type": true,
      "is_forwarded": true,
      "is_ordinal_only": false
    },
    "signature": {
      "return_type": "unsigned",
      "parameters": "size_t errorCode",
      "calling_convention": null,
      "full_prototype": "ZDICTLIB_API unsigned ZDICT_isError(size_t errorCode)"
    },
    "documentation": {
      "summary": "",
      "description": "",
      "source_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zdict.h",
      "source_line": 271
    },
    "evidence": {
      "discovered_by": "export_analyzer",
      "header_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zdict.h",
      "forwarded_to": null,
      "demangled_name": null
    },
    "mcp": {
      "version": "1.0",
      "input_schema": {
        "type": "object",
        "properties": {
          "errorCode": {
            "type": "integer",
            "description": "size_t"
          }
        },
        "required": [
          "errorCode"
        ]
      },
      "execution": {
        "method": "dll_import",
        "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
        "function_name": "ZDICT_isError",
        "calling_convention": "stdcall",
        "charset": "ansi"
      }
    },
    "metadata": {
      "is_signed": false,
      "publisher": null
    },
    "execution": {
      "method": "dll_import",
      "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
      "function_name": "ZDICT_isError",
      "calling_convention": "stdcall",
      "charset": "ansi"
    },
    "return_type": "unsigned",
    "parameters": [
      {
        "name": "errorCode",
        "type": "size_t",
        "required": true,
        "description": "size_t"
      }
    ]
  },
  {
    "name": "ZDICT_optimizeTrainFromBuffer_cover",
    "tool_id": "dll_ZDICT_optimizeTrainFromBuffer_cover_7",
    "kind": "export",
    "ordinal": 7,
    "rva": "00070030",
    "confidence": "guaranteed",
    "confidence_factors": {
      "has_signature": true,
      "has_documentation": true,
      "has_parameters": true,
      "has_return_type": true,
      "is_forwarded": true,
      "is_ordinal_only": false
    },
    "signature": {
      "return_type": "size_t",
      "parameters": "void* dictBuffer, size_t dictBufferCapacity, const void* samplesBuffer, const size_t* samplesSizes, unsigned nbSamples, ZDICT_cover_params_t* parameters",
      "calling_convention": null,
      "full_prototype": "ZDICTLIB_STATIC_API size_t ZDICT_optimizeTrainFromBuffer_cover( void* dictBuffer, size_t dictBufferCapacity, const void* samplesBuffer, const size_t* samplesSizes, unsigned nbSamples, ZDICT_cover_params_t* parameters)"
    },
    "documentation": {
      "summary": "! ZDICT_optimizeTrainFromBuffer_cover():\nThe same requirements as above hold for all the parameters except `parameters`.\nThis function tries many parameter combinations and picks the best parameters.\n`*parameters` is filled with the best parameters found,\ndictionary constructed with those parameters is stored in `dictBuffer`.\n\nAll of the parameters d, k, steps are optional.\nIf d is non-zero then we don't check multiple values of d, otherwise we check d = {6, 8}.\nif steps is zero it defaults to its default value.\nIf k is non-zero then we don't check multiple values of k, otherwise we check steps values in [50, 2000].\n\n@return: size of dictionary stored into `dictBuffer` (<= `dictBufferCapacity`)\n         or an error code, which can be tested with ZDICT_isError().\n         On success `*parameters` contains the parameters selected.\n         See ZDICT_trainFromBuffer() for details on failure modes.\nNote: ZDICT_optimizeTrainFromBuffer_cover() requires about 8 bytes of memory for each input byte and additionally another 5 bytes of memory for each byte of memory for each thread.",
      "description": "! ZDICT_optimizeTrainFromBuffer_cover():\nThe same requirements as above hold for all the parameters except `parameters`.\nThis function tries many parameter combinations and picks the best parameters.\n`*parameters` is filled with the best parameters found,\ndictionary constructed with those parameters is stored in `dictBuffer`.\n\nAll of the parameters d, k, steps are optional.\nIf d is non-zero then we don't check multiple values of d, otherwise we check d = {6, 8}.\nif steps is zero it defaults to its default value.\nIf k is non-zero then we don't check multiple values of k, otherwise we check steps values in [50, 2000].\n\n@return: size of dictionary stored into `dictBuffer` (<= `dictBufferCapacity`)\n         or an error code, which can be tested with ZDICT_isError().\n         On success `*parameters` contains the parameters selected.\n         See ZDICT_trainFromBuffer() for details on failure modes.\nNote: ZDICT_optimizeTrainFromBuffer_cover() requires about 8 bytes of memory for each input byte and additionally another 5 bytes of memory for each byte of memory for each thread.",
      "source_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zdict.h",
      "source_line": 374
    },
    "evidence": {
      "discovered_by": "export_analyzer",
      "header_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zdict.h",
      "forwarded_to": null,
      "demangled_name": null
    },
    "mcp": {
      "version": "1.0",
      "input_schema": {
        "type": "object",
        "properties": {
          "dictBuffer": {
            "type": "string",
            "description": "void*"
          },
          "dictBufferCapacity": {
            "type": "integer",
            "description": "size_t"
          },
          "samplesBuffer": {
            "type": "string",
            "description": "void*"
          },
          "samplesSizes": {
            "type": "string",
            "description": "size_t*"
          },
          "nbSamples": {
            "type": "integer",
            "description": "unsigned"
          },
          "parameters": {
            "type": "string",
            "description": "ZDICT_cover_params_t*"
          }
        },
        "required": [
          "dictBuffer",
          "dictBufferCapacity",
          "samplesBuffer",
          "samplesSizes",
          "nbSamples",
          "parameters"
        ]
      },
      "execution": {
        "method": "dll_import",
        "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
        "function_name": "ZDICT_optimizeTrainFromBuffer_cover",
        "calling_convention": "stdcall",
        "charset": "ansi"
      }
    },
    "metadata": {
      "is_signed": false,
      "publisher": null
    },
    "execution": {
      "method": "dll_import",
      "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
      "function_name": "ZDICT_optimizeTrainFromBuffer_cover",
      "calling_convention": "stdcall",
      "charset": "ansi"
    },
    "return_type": "size_t",
    "parameters": [
      {
        "name": "dictBuffer",
        "type": "void*",
        "required": true,
        "description": "void*"
      },
      {
        "name": "dictBufferCapacity",
        "type": "size_t",
        "required": true,
        "description": "size_t"
      },
      {
        "name": "samplesBuffer",
        "type": "const void*",
        "required": true,
        "description": "const void*"
      },
      {
        "name": "samplesSizes",
        "type": "const size_t*",
        "required": true,
        "description": "const size_t*"
      },
      {
        "name": "nbSamples",
        "type": "unsigned",
        "required": true,
        "description": "unsigned"
      },
      {
        "name": "parameters",
        "type": "ZDICT_cover_params_t*",
        "required": true,
        "description": "ZDICT_cover_params_t*"
      }
    ]
  },
  {
    "name": "ZDICT_optimizeTrainFromBuffer_fastCover",
    "tool_id": "dll_ZDICT_optimizeTrainFromBuffer_fastCover_8",
    "kind": "export",
    "ordinal": 8,
    "rva": "000767A0",
    "confidence": "guaranteed",
    "confidence_factors": {
      "has_signature": true,
      "has_documentation": true,
      "has_parameters": true,
      "has_return_type": true,
      "is_forwarded": true,
      "is_ordinal_only": false
    },
    "signature": {
      "return_type": "size_t",
      "parameters": "void* dictBuffer, size_t dictBufferCapacity, const void* samplesBuffer, const size_t* samplesSizes, unsigned nbSamples, ZDICT_fastCover_params_t* parameters",
      "calling_convention": null,
      "full_prototype": "ZDICTLIB_STATIC_API size_t ZDICT_optimizeTrainFromBuffer_fastCover(void* dictBuffer, size_t dictBufferCapacity, const void* samplesBuffer, const size_t* samplesSizes, unsigned nbSamples, ZDICT_fastCover_params_t* parameters)"
    },
    "documentation": {
      "summary": "! ZDICT_optimizeTrainFromBuffer_fastCover():\nThe same requirements as above hold for all the parameters except `parameters`.\nThis function tries many parameter combinations (specifically, k and d combinations)\nand picks the best parameters. `*parameters` is filled with the best parameters found,\ndictionary constructed with those parameters is stored in `dictBuffer`.\nAll of the parameters d, k, steps, f, and accel are optional.\nIf d is non-zero then we don't check multiple values of d, otherwise we check d = {6, 8}.\nif steps is zero it defaults to its default value.\nIf k is non-zero then we don't check multiple values of k, otherwise we check steps values in [50, 2000].\nIf f is zero, default value of 20 is used.\nIf accel is zero, default value of 1 is used.\n\n@return: size of dictionary stored into `dictBuffer` (<= `dictBufferCapacity`)\n         or an error code, which can be tested with ZDICT_isError().\n         On success `*parameters` contains the parameters selected.\n         See ZDICT_trainFromBuffer() for details on failure modes.\nNote: ZDICT_optimizeTrainFromBuffer_fastCover() requires about 6 * 2^f bytes of memory for each thread.",
      "description": "! ZDICT_optimizeTrainFromBuffer_fastCover():\nThe same requirements as above hold for all the parameters except `parameters`.\nThis function tries many parameter combinations (specifically, k and d combinations)\nand picks the best parameters. `*parameters` is filled with the best parameters found,\ndictionary constructed with those parameters is stored in `dictBuffer`.\nAll of the parameters d, k, steps, f, and accel are optional.\nIf d is non-zero then we don't check multiple values of d, otherwise we check d = {6, 8}.\nif steps is zero it defaults to its default value.\nIf k is non-zero then we don't check multiple values of k, otherwise we check steps values in [50, 2000].\nIf f is zero, default value of 20 is used.\nIf accel is zero, default value of 1 is used.\n\n@return: size of dictionary stored into `dictBuffer` (<= `dictBufferCapacity`)\n         or an error code, which can be tested with ZDICT_isError().\n         On success `*parameters` contains the parameters selected.\n         See ZDICT_trainFromBuffer() for details on failure modes.\nNote: ZDICT_optimizeTrainFromBuffer_fastCover() requires about 6 * 2^f bytes of memory for each thread.",
      "source_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zdict.h",
      "source_line": 418
    },
    "evidence": {
      "discovered_by": "export_analyzer",
      "header_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zdict.h",
      "forwarded_to": null,
      "demangled_name": null
    },
    "mcp": {
      "version": "1.0",
      "input_schema": {
        "type": "object",
        "properties": {
          "dictBuffer": {
            "type": "string",
            "description": "void*"
          },
          "dictBufferCapacity": {
            "type": "integer",
            "description": "size_t"
          },
          "samplesBuffer": {
            "type": "string",
            "description": "void*"
          },
          "samplesSizes": {
            "type": "string",
            "description": "size_t*"
          },
          "nbSamples": {
            "type": "integer",
            "description": "unsigned"
          },
          "parameters": {
            "type": "string",
            "description": "ZDICT_fastCover_params_t*"
          }
        },
        "required": [
          "dictBuffer",
          "dictBufferCapacity",
          "samplesBuffer",
          "samplesSizes",
          "nbSamples",
          "parameters"
        ]
      },
      "execution": {
        "method": "dll_import",
        "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
        "function_name": "ZDICT_optimizeTrainFromBuffer_fastCover",
        "calling_convention": "stdcall",
        "charset": "ansi"
      }
    },
    "metadata": {
      "is_signed": false,
      "publisher": null
    },
    "execution": {
      "method": "dll_import",
      "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
      "function_name": "ZDICT_optimizeTrainFromBuffer_fastCover",
      "calling_convention": "stdcall",
      "charset": "ansi"
    },
    "return_type": "size_t",
    "parameters": [
      {
        "name": "dictBuffer",
        "type": "void*",
        "required": true,
        "description": "void*"
      },
      {
        "name": "dictBufferCapacity",
        "type": "size_t",
        "required": true,
        "description": "size_t"
      },
      {
        "name": "samplesBuffer",
        "type": "const void*",
        "required": true,
        "description": "const void*"
      },
      {
        "name": "samplesSizes",
        "type": "const size_t*",
        "required": true,
        "description": "const size_t*"
      },
      {
        "name": "nbSamples",
        "type": "unsigned",
        "required": true,
        "description": "unsigned"
      },
      {
        "name": "parameters",
        "type": "ZDICT_fastCover_params_t*",
        "required": true,
        "description": "ZDICT_fastCover_params_t*"
      }
    ]
  },
  {
    "name": "ZDICT_trainFromBuffer",
    "tool_id": "dll_ZDICT_trainFromBuffer_9",
    "kind": "export",
    "ordinal": 9,
    "rva": "000793C0",
    "confidence": "guaranteed",
    "confidence_factors": {
      "has_signature": true,
      "has_documentation": true,
      "has_parameters": true,
      "has_return_type": true,
      "is_forwarded": true,
      "is_ordinal_only": false
    },
    "signature": {
      "return_type": "size_t",
      "parameters": "void* dictBuffer, size_t dictBufferCapacity, const void* samplesBuffer, const size_t* samplesSizes, unsigned nbSamples",
      "calling_convention": null,
      "full_prototype": "ZDICTLIB_API size_t ZDICT_trainFromBuffer(void* dictBuffer, size_t dictBufferCapacity, const void* samplesBuffer, const size_t* samplesSizes, unsigned nbSamples)"
    },
    "documentation": {
      "summary": "! ZDICT_trainFromBuffer():\n Train a dictionary from an array of samples.\n Redirect towards ZDICT_optimizeTrainFromBuffer_fastCover() single-threaded, with d=8, steps=4,\n f=20, and accel=1.\n Samples must be stored concatenated in a single flat buffer `samplesBuffer`,\n supplied with an array of sizes `samplesSizes`, providing the size of each sample, in order.\n The resulting dictionary will be saved into `dictBuffer`.\n@return: size of dictionary stored into `dictBuffer` (<= `dictBufferCapacity`)\n         or an error code, which can be tested with ZDICT_isError().\n Note:  Dictionary training will fail if there are not enough samples to construct a\n        dictionary, or if most of the samples are too small (< 8 bytes being the lower limit).\n        If dictionary training fails, you should use zstd without a dictionary, as the dictionary\n        would've been ineffective anyways. If you believe your samples would benefit from a dictionary\n        please open an issue with details, and we can look into it.\n Note: ZDICT_trainFromBuffer()'s memory usage is about 6 MB.\n Tips: In general, a reasonable dictionary has a size of ~ 100 KB.\n       It's possible to select smaller or larger size, just by specifying `dictBufferCapacity`.\n       In general, it's recommended to provide a few thousands samples, though this can vary a lot.\n       It's recommended that total size of all samples be about ~x100 times the target size of dictionary.",
      "description": "! ZDICT_trainFromBuffer():\n Train a dictionary from an array of samples.\n Redirect towards ZDICT_optimizeTrainFromBuffer_fastCover() single-threaded, with d=8, steps=4,\n f=20, and accel=1.\n Samples must be stored concatenated in a single flat buffer `samplesBuffer`,\n supplied with an array of sizes `samplesSizes`, providing the size of each sample, in order.\n The resulting dictionary will be saved into `dictBuffer`.\n@return: size of dictionary stored into `dictBuffer` (<= `dictBufferCapacity`)\n         or an error code, which can be tested with ZDICT_isError().\n Note:  Dictionary training will fail if there are not enough samples to construct a\n        dictionary, or if most of the samples are too small (< 8 bytes being the lower limit).\n        If dictionary training fails, you should use zstd without a dictionary, as the dictionary\n        would've been ineffective anyways. If you believe your samples would benefit from a dictionary\n        please open an issue with details, and we can look into it.\n Note: ZDICT_trainFromBuffer()'s memory usage is about 6 MB.\n Tips: In general, a reasonable dictionary has a size of ~ 100 KB.\n       It's possible to select smaller or larger size, just by specifying `dictBufferCapacity`.\n       In general, it's recommended to provide a few thousands samples, though this can vary a lot.\n       It's recommended that total size of all samples be about ~x100 times the target size of dictionary.",
      "source_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zdict.h",
      "source_line": 210
    },
    "evidence": {
      "discovered_by": "export_analyzer",
      "header_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zdict.h",
      "forwarded_to": null,
      "demangled_name": null
    },
    "mcp": {
      "version": "1.0",
      "input_schema": {
        "type": "object",
        "properties": {
          "dictBuffer": {
            "type": "string",
            "description": "void*"
          },
          "dictBufferCapacity": {
            "type": "integer",
            "description": "size_t"
          },
          "samplesBuffer": {
            "type": "string",
            "description": "void*"
          },
          "samplesSizes": {
            "type": "string",
            "description": "size_t*"
          },
          "nbSamples": {
            "type": "integer",
            "description": "unsigned"
          }
        },
        "required": [
          "dictBuffer",
          "dictBufferCapacity",
          "samplesBuffer",
          "samplesSizes",
          "nbSamples"
        ]
      },
      "execution": {
        "method": "dll_import",
        "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
        "function_name": "ZDICT_trainFromBuffer",
        "calling_convention": "stdcall",
        "charset": "ansi"
      }
    },
    "metadata": {
      "is_signed": false,
      "publisher": null
    },
    "execution": {
      "method": "dll_import",
      "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
      "function_name": "ZDICT_trainFromBuffer",
      "calling_convention": "stdcall",
      "charset": "ansi"
    },
    "return_type": "size_t",
    "parameters": [
      {
        "name": "dictBuffer",
        "type": "void*",
        "required": true,
        "description": "void*"
      },
      {
        "name": "dictBufferCapacity",
        "type": "size_t",
        "required": true,
        "description": "size_t"
      },
      {
        "name": "samplesBuffer",
        "type": "const void*",
        "required": true,
        "description": "const void*"
      },
      {
        "name": "samplesSizes",
        "type": "const size_t*",
        "required": true,
        "description": "const size_t*"
      },
      {
        "name": "nbSamples",
        "type": "unsigned",
        "required": true,
        "description": "unsigned"
      }
    ]
  },
  {
    "name": "ZDICT_trainFromBuffer_cover",
    "tool_id": "dll_ZDICT_trainFromBuffer_cover_10",
    "kind": "export",
    "ordinal": 10,
    "rva": "000708D0",
    "confidence": "guaranteed",
    "confidence_factors": {
      "has_signature": true,
      "has_documentation": true,
      "has_parameters": true,
      "has_return_type": true,
      "is_forwarded": true,
      "is_ordinal_only": false
    },
    "signature": {
      "return_type": "size_t",
      "parameters": "void *dictBuffer, size_t dictBufferCapacity, const void *samplesBuffer, const size_t *samplesSizes, unsigned nbSamples, ZDICT_cover_params_t parameters",
      "calling_convention": null,
      "full_prototype": "ZDICTLIB_STATIC_API size_t ZDICT_trainFromBuffer_cover( void *dictBuffer, size_t dictBufferCapacity, const void *samplesBuffer, const size_t *samplesSizes, unsigned nbSamples, ZDICT_cover_params_t parameters)"
    },
    "documentation": {
      "summary": "! ZDICT_trainFromBuffer_cover():\n Train a dictionary from an array of samples using the COVER algorithm.\n Samples must be stored concatenated in a single flat buffer `samplesBuffer`,\n supplied with an array of sizes `samplesSizes`, providing the size of each sample, in order.\n The resulting dictionary will be saved into `dictBuffer`.\n@return: size of dictionary stored into `dictBuffer` (<= `dictBufferCapacity`)\n         or an error code, which can be tested with ZDICT_isError().\n         See ZDICT_trainFromBuffer() for details on failure modes.\n Note: ZDICT_trainFromBuffer_cover() requires about 9 bytes of memory for each input byte.\n Tips: In general, a reasonable dictionary has a size of ~ 100 KB.\n       It's possible to select smaller or larger size, just by specifying `dictBufferCapacity`.\n       In general, it's recommended to provide a few thousands samples, though this can vary a lot.\n       It's recommended that total size of all samples be about ~x100 times the target size of dictionary.",
      "description": "! ZDICT_trainFromBuffer_cover():\n Train a dictionary from an array of samples using the COVER algorithm.\n Samples must be stored concatenated in a single flat buffer `samplesBuffer`,\n supplied with an array of sizes `samplesSizes`, providing the size of each sample, in order.\n The resulting dictionary will be saved into `dictBuffer`.\n@return: size of dictionary stored into `dictBuffer` (<= `dictBufferCapacity`)\n         or an error code, which can be tested with ZDICT_isError().\n         See ZDICT_trainFromBuffer() for details on failure modes.\n Note: ZDICT_trainFromBuffer_cover() requires about 9 bytes of memory for each input byte.\n Tips: In general, a reasonable dictionary has a size of ~ 100 KB.\n       It's possible to select smaller or larger size, just by specifying `dictBufferCapacity`.\n       In general, it's recommended to provide a few thousands samples, though this can vary a lot.\n       It's recommended that total size of all samples be about ~x100 times the target size of dictionary.",
      "source_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zdict.h",
      "source_line": 352
    },
    "evidence": {
      "discovered_by": "export_analyzer",
      "header_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zdict.h",
      "forwarded_to": null,
      "demangled_name": null
    },
    "mcp": {
      "version": "1.0",
      "input_schema": {
        "type": "object",
        "properties": {
          "dictBuffer": {
            "type": "string",
            "description": "void"
          },
          "dictBufferCapacity": {
            "type": "integer",
            "description": "size_t"
          },
          "samplesBuffer": {
            "type": "string",
            "description": "void"
          },
          "samplesSizes": {
            "type": "integer",
            "description": "size_t"
          },
          "nbSamples": {
            "type": "integer",
            "description": "unsigned"
          },
          "parameters": {
            "type": "string",
            "description": "ZDICT_cover_params_t"
          }
        },
        "required": [
          "dictBuffer",
          "dictBufferCapacity",
          "samplesBuffer",
          "samplesSizes",
          "nbSamples",
          "parameters"
        ]
      },
      "execution": {
        "method": "dll_import",
        "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
        "function_name": "ZDICT_trainFromBuffer_cover",
        "calling_convention": "stdcall",
        "charset": "ansi"
      }
    },
    "metadata": {
      "is_signed": false,
      "publisher": null
    },
    "execution": {
      "method": "dll_import",
      "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
      "function_name": "ZDICT_trainFromBuffer_cover",
      "calling_convention": "stdcall",
      "charset": "ansi"
    },
    "return_type": "size_t",
    "parameters": [
      {
        "name": "dictBuffer",
        "type": "void",
        "required": true,
        "description": "void"
      },
      {
        "name": "dictBufferCapacity",
        "type": "size_t",
        "required": true,
        "description": "size_t"
      },
      {
        "name": "samplesBuffer",
        "type": "const void",
        "required": true,
        "description": "const void"
      },
      {
        "name": "samplesSizes",
        "type": "const size_t",
        "required": true,
        "description": "const size_t"
      },
      {
        "name": "nbSamples",
        "type": "unsigned",
        "required": true,
        "description": "unsigned"
      },
      {
        "name": "parameters",
        "type": "ZDICT_cover_params_t",
        "required": true,
        "description": "ZDICT_cover_params_t"
      }
    ]
  },
  {
    "name": "ZDICT_trainFromBuffer_fastCover",
    "tool_id": "dll_ZDICT_trainFromBuffer_fastCover_11",
    "kind": "export",
    "ordinal": 11,
    "rva": "00076FE0",
    "confidence": "guaranteed",
    "confidence_factors": {
      "has_signature": true,
      "has_documentation": true,
      "has_parameters": true,
      "has_return_type": true,
      "is_forwarded": true,
      "is_ordinal_only": false
    },
    "signature": {
      "return_type": "size_t",
      "parameters": "void *dictBuffer, size_t dictBufferCapacity, const void *samplesBuffer, const size_t *samplesSizes, unsigned nbSamples, ZDICT_fastCover_params_t parameters",
      "calling_convention": null,
      "full_prototype": "ZDICTLIB_STATIC_API size_t ZDICT_trainFromBuffer_fastCover(void *dictBuffer, size_t dictBufferCapacity, const void *samplesBuffer, const size_t *samplesSizes, unsigned nbSamples, ZDICT_fastCover_params_t parameters)"
    },
    "documentation": {
      "summary": "! ZDICT_trainFromBuffer_fastCover():\n Train a dictionary from an array of samples using a modified version of COVER algorithm.\n Samples must be stored concatenated in a single flat buffer `samplesBuffer`,\n supplied with an array of sizes `samplesSizes`, providing the size of each sample, in order.\n d and k are required.\n All other parameters are optional, will use default values if not provided\n The resulting dictionary will be saved into `dictBuffer`.\n@return: size of dictionary stored into `dictBuffer` (<= `dictBufferCapacity`)\n         or an error code, which can be tested with ZDICT_isError().\n         See ZDICT_trainFromBuffer() for details on failure modes.\n Note: ZDICT_trainFromBuffer_fastCover() requires 6 * 2^f bytes of memory.\n Tips: In general, a reasonable dictionary has a size of ~ 100 KB.\n       It's possible to select smaller or larger size, just by specifying `dictBufferCapacity`.\n       In general, it's recommended to provide a few thousands samples, though this can vary a lot.\n       It's recommended that total size of all samples be about ~x100 times the target size of dictionary.",
      "description": "! ZDICT_trainFromBuffer_fastCover():\n Train a dictionary from an array of samples using a modified version of COVER algorithm.\n Samples must be stored concatenated in a single flat buffer `samplesBuffer`,\n supplied with an array of sizes `samplesSizes`, providing the size of each sample, in order.\n d and k are required.\n All other parameters are optional, will use default values if not provided\n The resulting dictionary will be saved into `dictBuffer`.\n@return: size of dictionary stored into `dictBuffer` (<= `dictBufferCapacity`)\n         or an error code, which can be tested with ZDICT_isError().\n         See ZDICT_trainFromBuffer() for details on failure modes.\n Note: ZDICT_trainFromBuffer_fastCover() requires 6 * 2^f bytes of memory.\n Tips: In general, a reasonable dictionary has a size of ~ 100 KB.\n       It's possible to select smaller or larger size, just by specifying `dictBufferCapacity`.\n       In general, it's recommended to provide a few thousands samples, though this can vary a lot.\n       It's recommended that total size of all samples be about ~x100 times the target size of dictionary.",
      "source_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zdict.h",
      "source_line": 395
    },
    "evidence": {
      "discovered_by": "export_analyzer",
      "header_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zdict.h",
      "forwarded_to": null,
      "demangled_name": null
    },
    "mcp": {
      "version": "1.0",
      "input_schema": {
        "type": "object",
        "properties": {
          "dictBuffer": {
            "type": "string",
            "description": "void"
          },
          "dictBufferCapacity": {
            "type": "integer",
            "description": "size_t"
          },
          "samplesBuffer": {
            "type": "string",
            "description": "void"
          },
          "samplesSizes": {
            "type": "integer",
            "description": "size_t"
          },
          "nbSamples": {
            "type": "integer",
            "description": "unsigned"
          },
          "parameters": {
            "type": "string",
            "description": "ZDICT_fastCover_params_t"
          }
        },
        "required": [
          "dictBuffer",
          "dictBufferCapacity",
          "samplesBuffer",
          "samplesSizes",
          "nbSamples",
          "parameters"
        ]
      },
      "execution": {
        "method": "dll_import",
        "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
        "function_name": "ZDICT_trainFromBuffer_fastCover",
        "calling_convention": "stdcall",
        "charset": "ansi"
      }
    },
    "metadata": {
      "is_signed": false,
      "publisher": null
    },
    "execution": {
      "method": "dll_import",
      "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
      "function_name": "ZDICT_trainFromBuffer_fastCover",
      "calling_convention": "stdcall",
      "charset": "ansi"
    },
    "return_type": "size_t",
    "parameters": [
      {
        "name": "dictBuffer",
        "type": "void",
        "required": true,
        "description": "void"
      },
      {
        "name": "dictBufferCapacity",
        "type": "size_t",
        "required": true,
        "description": "size_t"
      },
      {
        "name": "samplesBuffer",
        "type": "const void",
        "required": true,
        "description": "const void"
      },
      {
        "name": "samplesSizes",
        "type": "const size_t",
        "required": true,
        "description": "const size_t"
      },
      {
        "name": "nbSamples",
        "type": "unsigned",
        "required": true,
        "description": "unsigned"
      },
      {
        "name": "parameters",
        "type": "ZDICT_fastCover_params_t",
        "required": true,
        "description": "ZDICT_fastCover_params_t"
      }
    ]
  },
  {
    "name": "ZDICT_trainFromBuffer_legacy",
    "tool_id": "dll_ZDICT_trainFromBuffer_legacy_12",
    "kind": "export",
    "ordinal": 12,
    "rva": "00079430",
    "confidence": "guaranteed",
    "confidence_factors": {
      "has_signature": true,
      "has_documentation": true,
      "has_parameters": true,
      "has_return_type": true,
      "is_forwarded": true,
      "is_ordinal_only": false
    },
    "signature": {
      "return_type": "size_t",
      "parameters": "void* dictBuffer, size_t dictBufferCapacity, const void* samplesBuffer, const size_t* samplesSizes, unsigned nbSamples, ZDICT_legacy_params_t parameters",
      "calling_convention": null,
      "full_prototype": "ZDICTLIB_STATIC_API size_t ZDICT_trainFromBuffer_legacy( void* dictBuffer, size_t dictBufferCapacity, const void* samplesBuffer, const size_t* samplesSizes, unsigned nbSamples, ZDICT_legacy_params_t parameters)"
    },
    "documentation": {
      "summary": "! ZDICT_trainFromBuffer_legacy():\n Train a dictionary from an array of samples.\n Samples must be stored concatenated in a single flat buffer `samplesBuffer`,\n supplied with an array of sizes `samplesSizes`, providing the size of each sample, in order.\n The resulting dictionary will be saved into `dictBuffer`.\n`parameters` is optional and can be provided with values set to 0 to mean \"default\".\n@return: size of dictionary stored into `dictBuffer` (<= `dictBufferCapacity`)\n         or an error code, which can be tested with ZDICT_isError().\n         See ZDICT_trainFromBuffer() for details on failure modes.\n Tips: In general, a reasonable dictionary has a size of ~ 100 KB.\n       It's possible to select smaller or larger size, just by specifying `dictBufferCapacity`.\n       In general, it's recommended to provide a few thousands samples, though this can vary a lot.\n       It's recommended that total size of all samples be about ~x100 times the target size of dictionary.\n Note: ZDICT_trainFromBuffer_legacy() will send notifications into stderr if instructed to, using notificationLevel>0.",
      "description": "! ZDICT_trainFromBuffer_legacy():\n Train a dictionary from an array of samples.\n Samples must be stored concatenated in a single flat buffer `samplesBuffer`,\n supplied with an array of sizes `samplesSizes`, providing the size of each sample, in order.\n The resulting dictionary will be saved into `dictBuffer`.\n`parameters` is optional and can be provided with values set to 0 to mean \"default\".\n@return: size of dictionary stored into `dictBuffer` (<= `dictBufferCapacity`)\n         or an error code, which can be tested with ZDICT_isError().\n         See ZDICT_trainFromBuffer() for details on failure modes.\n Tips: In general, a reasonable dictionary has a size of ~ 100 KB.\n       It's possible to select smaller or larger size, just by specifying `dictBufferCapacity`.\n       In general, it's recommended to provide a few thousands samples, though this can vary a lot.\n       It's recommended that total size of all samples be about ~x100 times the target size of dictionary.\n Note: ZDICT_trainFromBuffer_legacy() will send notifications into stderr if instructed to, using notificationLevel>0.",
      "source_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zdict.h",
      "source_line": 443
    },
    "evidence": {
      "discovered_by": "export_analyzer",
      "header_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zdict.h",
      "forwarded_to": null,
      "demangled_name": null
    },
    "mcp": {
      "version": "1.0",
      "input_schema": {
        "type": "object",
        "properties": {
          "dictBuffer": {
            "type": "string",
            "description": "void*"
          },
          "dictBufferCapacity": {
            "type": "integer",
            "description": "size_t"
          },
          "samplesBuffer": {
            "type": "string",
            "description": "void*"
          },
          "samplesSizes": {
            "type": "string",
            "description": "size_t*"
          },
          "nbSamples": {
            "type": "integer",
            "description": "unsigned"
          },
          "parameters": {
            "type": "string",
            "description": "ZDICT_legacy_params_t"
          }
        },
        "required": [
          "dictBuffer",
          "dictBufferCapacity",
          "samplesBuffer",
          "samplesSizes",
          "nbSamples",
          "parameters"
        ]
      },
      "execution": {
        "method": "dll_import",
        "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
        "function_name": "ZDICT_trainFromBuffer_legacy",
        "calling_convention": "stdcall",
        "charset": "ansi"
      }
    },
    "metadata": {
      "is_signed": false,
      "publisher": null
    },
    "execution": {
      "method": "dll_import",
      "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
      "function_name": "ZDICT_trainFromBuffer_legacy",
      "calling_convention": "stdcall",
      "charset": "ansi"
    },
    "return_type": "size_t",
    "parameters": [
      {
        "name": "dictBuffer",
        "type": "void*",
        "required": true,
        "description": "void*"
      },
      {
        "name": "dictBufferCapacity",
        "type": "size_t",
        "required": true,
        "description": "size_t"
      },
      {
        "name": "samplesBuffer",
        "type": "const void*",
        "required": true,
        "description": "const void*"
      },
      {
        "name": "samplesSizes",
        "type": "const size_t*",
        "required": true,
        "description": "const size_t*"
      },
      {
        "name": "nbSamples",
        "type": "unsigned",
        "required": true,
        "description": "unsigned"
      },
      {
        "name": "parameters",
        "type": "ZDICT_legacy_params_t",
        "required": true,
        "description": "ZDICT_legacy_params_t"
      }
    ]
  },
  {
    "name": "ZSTD_CCtxParams_getParameter",
    "tool_id": "dll_ZSTD_CCtxParams_getParameter_13",
    "kind": "export",
    "ordinal": 13,
    "rva": "000081E0",
    "confidence": "guaranteed",
    "confidence_factors": {
      "has_signature": true,
      "has_documentation": true,
      "has_parameters": true,
      "has_return_type": true,
      "is_forwarded": true,
      "is_ordinal_only": false
    },
    "signature": {
      "return_type": "size_t",
      "parameters": "const ZSTD_CCtx_params* params, ZSTD_cParameter param, int* value",
      "calling_convention": null,
      "full_prototype": "ZSTDLIB_STATIC_API size_t ZSTD_CCtxParams_getParameter(const ZSTD_CCtx_params* params, ZSTD_cParameter param, int* value)"
    },
    "documentation": {
      "summary": "! ZSTD_CCtxParams_getParameter() :\nSimilar to ZSTD_CCtx_getParameter.\nGet the requested value of one compression parameter, selected by enum ZSTD_cParameter.\n@result : 0, or an error code (which can be tested with ZSTD_isError()).",
      "description": "! ZSTD_CCtxParams_getParameter() :\nSimilar to ZSTD_CCtx_getParameter.\nGet the requested value of one compression parameter, selected by enum ZSTD_cParameter.\n@result : 0, or an error code (which can be tested with ZSTD_isError()).",
      "source_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "source_line": 2419
    },
    "evidence": {
      "discovered_by": "export_analyzer",
      "header_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "forwarded_to": null,
      "demangled_name": null
    },
    "mcp": {
      "version": "1.0",
      "input_schema": {
        "type": "object",
        "properties": {
          "params": {
            "type": "string",
            "description": "ZSTD_CCtx_params*"
          },
          "param": {
            "type": "string",
            "description": "ZSTD_cParameter"
          },
          "value": {
            "type": "string",
            "description": "int*"
          }
        },
        "required": [
          "params",
          "param",
          "value"
        ]
      },
      "execution": {
        "method": "dll_import",
        "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
        "function_name": "ZSTD_CCtxParams_getParameter",
        "calling_convention": "stdcall",
        "charset": "ansi"
      }
    },
    "metadata": {
      "is_signed": false,
      "publisher": null
    },
    "execution": {
      "method": "dll_import",
      "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
      "function_name": "ZSTD_CCtxParams_getParameter",
      "calling_convention": "stdcall",
      "charset": "ansi"
    },
    "return_type": "size_t",
    "parameters": [
      {
        "name": "params",
        "type": "const ZSTD_CCtx_params*",
        "required": true,
        "description": "const ZSTD_CCtx_params*"
      },
      {
        "name": "param",
        "type": "ZSTD_cParameter",
        "required": true,
        "description": "ZSTD_cParameter"
      },
      {
        "name": "value",
        "type": "int*",
        "required": true,
        "description": "int*"
      }
    ]
  },
  {
    "name": "ZSTD_CCtxParams_init",
    "tool_id": "dll_ZSTD_CCtxParams_init_14",
    "kind": "export",
    "ordinal": 14,
    "rva": "00008560",
    "confidence": "guaranteed",
    "confidence_factors": {
      "has_signature": true,
      "has_documentation": true,
      "has_parameters": true,
      "has_return_type": true,
      "is_forwarded": true,
      "is_ordinal_only": false
    },
    "signature": {
      "return_type": "size_t",
      "parameters": "ZSTD_CCtx_params* cctxParams, int compressionLevel",
      "calling_convention": null,
      "full_prototype": "ZSTDLIB_STATIC_API size_t ZSTD_CCtxParams_init(ZSTD_CCtx_params* cctxParams, int compressionLevel)"
    },
    "documentation": {
      "summary": "! ZSTD_CCtxParams_init() :\n Initializes the compression parameters of cctxParams according to\n compression level. All other parameters are reset to their default values.",
      "description": "! ZSTD_CCtxParams_init() :\n Initializes the compression parameters of cctxParams according to\n compression level. All other parameters are reset to their default values.",
      "source_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "source_line": 2396
    },
    "evidence": {
      "discovered_by": "export_analyzer",
      "header_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "forwarded_to": null,
      "demangled_name": null
    },
    "mcp": {
      "version": "1.0",
      "input_schema": {
        "type": "object",
        "properties": {
          "cctxParams": {
            "type": "string",
            "description": "ZSTD_CCtx_params*"
          },
          "compressionLevel": {
            "type": "integer",
            "description": "int"
          }
        },
        "required": [
          "cctxParams",
          "compressionLevel"
        ]
      },
      "execution": {
        "method": "dll_import",
        "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
        "function_name": "ZSTD_CCtxParams_init",
        "calling_convention": "stdcall",
        "charset": "ansi"
      }
    },
    "metadata": {
      "is_signed": false,
      "publisher": null
    },
    "execution": {
      "method": "dll_import",
      "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
      "function_name": "ZSTD_CCtxParams_init",
      "calling_convention": "stdcall",
      "charset": "ansi"
    },
    "return_type": "size_t",
    "parameters": [
      {
        "name": "cctxParams",
        "type": "ZSTD_CCtx_params*",
        "required": true,
        "description": "ZSTD_CCtx_params*"
      },
      {
        "name": "compressionLevel",
        "type": "int",
        "required": true,
        "description": "int"
      }
    ]
  },
  {
    "name": "ZSTD_CCtxParams_init_advanced",
    "tool_id": "dll_ZSTD_CCtxParams_init_advanced_15",
    "kind": "export",
    "ordinal": 15,
    "rva": "000085B0",
    "confidence": "guaranteed",
    "confidence_factors": {
      "has_signature": true,
      "has_documentation": true,
      "has_parameters": true,
      "has_return_type": true,
      "is_forwarded": true,
      "is_ordinal_only": false
    },
    "signature": {
      "return_type": "size_t",
      "parameters": "ZSTD_CCtx_params* cctxParams, ZSTD_parameters params",
      "calling_convention": null,
      "full_prototype": "ZSTDLIB_STATIC_API size_t ZSTD_CCtxParams_init_advanced(ZSTD_CCtx_params* cctxParams, ZSTD_parameters params)"
    },
    "documentation": {
      "summary": "! ZSTD_CCtxParams_init_advanced() :\n Initializes the compression and frame parameters of cctxParams according to\n params. All other parameters are reset to their default values.",
      "description": "! ZSTD_CCtxParams_init_advanced() :\n Initializes the compression and frame parameters of cctxParams according to\n params. All other parameters are reset to their default values.",
      "source_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "source_line": 2402
    },
    "evidence": {
      "discovered_by": "export_analyzer",
      "header_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "forwarded_to": null,
      "demangled_name": null
    },
    "mcp": {
      "version": "1.0",
      "input_schema": {
        "type": "object",
        "properties": {
          "cctxParams": {
            "type": "string",
            "description": "ZSTD_CCtx_params*"
          },
          "params": {
            "type": "string",
            "description": "ZSTD_parameters"
          }
        },
        "required": [
          "cctxParams",
          "params"
        ]
      },
      "execution": {
        "method": "dll_import",
        "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
        "function_name": "ZSTD_CCtxParams_init_advanced",
        "calling_convention": "stdcall",
        "charset": "ansi"
      }
    },
    "metadata": {
      "is_signed": false,
      "publisher": null
    },
    "execution": {
      "method": "dll_import",
      "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
      "function_name": "ZSTD_CCtxParams_init_advanced",
      "calling_convention": "stdcall",
      "charset": "ansi"
    },
    "return_type": "size_t",
    "parameters": [
      {
        "name": "cctxParams",
        "type": "ZSTD_CCtx_params*",
        "required": true,
        "description": "ZSTD_CCtx_params*"
      },
      {
        "name": "params",
        "type": "ZSTD_parameters",
        "required": true,
        "description": "ZSTD_parameters"
      }
    ]
  },
  {
    "name": "ZSTD_CCtxParams_registerSequenceProducer",
    "tool_id": "dll_ZSTD_CCtxParams_registerSequenceProducer_16",
    "kind": "export",
    "ordinal": 16,
    "rva": "00008790",
    "confidence": "guaranteed",
    "confidence_factors": {
      "has_signature": true,
      "has_documentation": true,
      "has_parameters": true,
      "has_return_type": true,
      "is_forwarded": true,
      "is_ordinal_only": false
    },
    "signature": {
      "return_type": "void",
      "parameters": "ZSTD_CCtx_params* params, void* sequenceProducerState, ZSTD_sequenceProducer_F sequenceProducer",
      "calling_convention": null,
      "full_prototype": "ZSTDLIB_STATIC_API void ZSTD_CCtxParams_registerSequenceProducer( ZSTD_CCtx_params* params, void* sequenceProducerState, ZSTD_sequenceProducer_F sequenceProducer )"
    },
    "documentation": {
      "summary": "! ZSTD_CCtxParams_registerSequenceProducer() :\nSame as ZSTD_registerSequenceProducer(), but operates on ZSTD_CCtx_params.\nThis is used for accurate size estimation with ZSTD_estimateCCtxSize_usingCCtxParams(),\nwhich is needed when creating a ZSTD_CCtx with ZSTD_initStaticCCtx().\n\nIf you are using the external sequence producer API in a scenario where ZSTD_initStaticCCtx()\nis required, then this function is for you. Otherwise, you probably don't need it.\n\nSee tests/zstreamtest.c for example usage.",
      "description": "! ZSTD_CCtxParams_registerSequenceProducer() :\nSame as ZSTD_registerSequenceProducer(), but operates on ZSTD_CCtx_params.\nThis is used for accurate size estimation with ZSTD_estimateCCtxSize_usingCCtxParams(),\nwhich is needed when creating a ZSTD_CCtx with ZSTD_initStaticCCtx().\n\nIf you are using the external sequence producer API in a scenario where ZSTD_initStaticCCtx()\nis required, then this function is for you. Otherwise, you probably don't need it.\n\nSee tests/zstreamtest.c for example usage.",
      "source_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "source_line": 2974
    },
    "evidence": {
      "discovered_by": "export_analyzer",
      "header_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "forwarded_to": null,
      "demangled_name": null
    },
    "mcp": {
      "version": "1.0",
      "input_schema": {
        "type": "object",
        "properties": {
          "params": {
            "type": "string",
            "description": "ZSTD_CCtx_params*"
          },
          "sequenceProducerState": {
            "type": "string",
            "description": "void*"
          },
          "sequenceProducer": {
            "type": "string",
            "description": "ZSTD_sequenceProducer_F"
          }
        },
        "required": [
          "params",
          "sequenceProducerState",
          "sequenceProducer"
        ]
      },
      "execution": {
        "method": "dll_import",
        "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
        "function_name": "ZSTD_CCtxParams_registerSequenceProducer",
        "calling_convention": "stdcall",
        "charset": "ansi"
      }
    },
    "metadata": {
      "is_signed": false,
      "publisher": null
    },
    "execution": {
      "method": "dll_import",
      "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
      "function_name": "ZSTD_CCtxParams_registerSequenceProducer",
      "calling_convention": "stdcall",
      "charset": "ansi"
    },
    "return_type": "void",
    "parameters": [
      {
        "name": "params",
        "type": "ZSTD_CCtx_params*",
        "required": true,
        "description": "ZSTD_CCtx_params*"
      },
      {
        "name": "sequenceProducerState",
        "type": "void*",
        "required": true,
        "description": "void*"
      },
      {
        "name": "sequenceProducer",
        "type": "ZSTD_sequenceProducer_F",
        "required": true,
        "description": "ZSTD_sequenceProducer_F"
      }
    ]
  },
  {
    "name": "ZSTD_CCtxParams_reset",
    "tool_id": "dll_ZSTD_CCtxParams_reset_17",
    "kind": "export",
    "ordinal": 17,
    "rva": "000087B0",
    "confidence": "guaranteed",
    "confidence_factors": {
      "has_signature": true,
      "has_documentation": true,
      "has_parameters": true,
      "has_return_type": true,
      "is_forwarded": true,
      "is_ordinal_only": false
    },
    "signature": {
      "return_type": "size_t",
      "parameters": "ZSTD_CCtx_params* params",
      "calling_convention": null,
      "full_prototype": "ZSTDLIB_STATIC_API size_t ZSTD_CCtxParams_reset(ZSTD_CCtx_params* params)"
    },
    "documentation": {
      "summary": "! ZSTD_CCtxParams_reset() :\n Reset params to default values.",
      "description": "! ZSTD_CCtxParams_reset() :\n Reset params to default values.",
      "source_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "source_line": 2390
    },
    "evidence": {
      "discovered_by": "export_analyzer",
      "header_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "forwarded_to": null,
      "demangled_name": null
    },
    "mcp": {
      "version": "1.0",
      "input_schema": {
        "type": "object",
        "properties": {
          "params": {
            "type": "string",
            "description": "ZSTD_CCtx_params*"
          }
        },
        "required": [
          "params"
        ]
      },
      "execution": {
        "method": "dll_import",
        "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
        "function_name": "ZSTD_CCtxParams_reset",
        "calling_convention": "stdcall",
        "charset": "ansi"
      }
    },
    "metadata": {
      "is_signed": false,
      "publisher": null
    },
    "execution": {
      "method": "dll_import",
      "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
      "function_name": "ZSTD_CCtxParams_reset",
      "calling_convention": "stdcall",
      "charset": "ansi"
    },
    "return_type": "size_t",
    "parameters": [
      {
        "name": "params",
        "type": "ZSTD_CCtx_params*",
        "required": true,
        "description": "ZSTD_CCtx_params*"
      }
    ]
  },
  {
    "name": "ZSTD_CCtxParams_setParameter",
    "tool_id": "dll_ZSTD_CCtxParams_setParameter_18",
    "kind": "export",
    "ordinal": 18,
    "rva": "000087F0",
    "confidence": "guaranteed",
    "confidence_factors": {
      "has_signature": true,
      "has_documentation": true,
      "has_parameters": true,
      "has_return_type": true,
      "is_forwarded": true,
      "is_ordinal_only": false
    },
    "signature": {
      "return_type": "size_t",
      "parameters": "ZSTD_CCtx_params* params, ZSTD_cParameter param, int value",
      "calling_convention": null,
      "full_prototype": "ZSTDLIB_STATIC_API size_t ZSTD_CCtxParams_setParameter(ZSTD_CCtx_params* params, ZSTD_cParameter param, int value)"
    },
    "documentation": {
      "summary": "! ZSTD_CCtxParams_setParameter() : Requires v1.4.0+\n Similar to ZSTD_CCtx_setParameter.\n Set one compression parameter, selected by enum ZSTD_cParameter.\n Parameters must be applied to a ZSTD_CCtx using\n ZSTD_CCtx_setParametersUsingCCtxParams().\n@result : a code representing success or failure (which can be tested with\n          ZSTD_isError()).",
      "description": "! ZSTD_CCtxParams_setParameter() : Requires v1.4.0+\n Similar to ZSTD_CCtx_setParameter.\n Set one compression parameter, selected by enum ZSTD_cParameter.\n Parameters must be applied to a ZSTD_CCtx using\n ZSTD_CCtx_setParametersUsingCCtxParams().\n@result : a code representing success or failure (which can be tested with\n          ZSTD_isError()).",
      "source_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "source_line": 2412
    },
    "evidence": {
      "discovered_by": "export_analyzer",
      "header_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "forwarded_to": null,
      "demangled_name": null
    },
    "mcp": {
      "version": "1.0",
      "input_schema": {
        "type": "object",
        "properties": {
          "params": {
            "type": "string",
            "description": "ZSTD_CCtx_params*"
          },
          "param": {
            "type": "string",
            "description": "ZSTD_cParameter"
          },
          "value": {
            "type": "integer",
            "description": "int"
          }
        },
        "required": [
          "params",
          "param",
          "value"
        ]
      },
      "execution": {
        "method": "dll_import",
        "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
        "function_name": "ZSTD_CCtxParams_setParameter",
        "calling_convention": "stdcall",
        "charset": "ansi"
      }
    },
    "metadata": {
      "is_signed": false,
      "publisher": null
    },
    "execution": {
      "method": "dll_import",
      "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
      "function_name": "ZSTD_CCtxParams_setParameter",
      "calling_convention": "stdcall",
      "charset": "ansi"
    },
    "return_type": "size_t",
    "parameters": [
      {
        "name": "params",
        "type": "ZSTD_CCtx_params*",
        "required": true,
        "description": "ZSTD_CCtx_params*"
      },
      {
        "name": "param",
        "type": "ZSTD_cParameter",
        "required": true,
        "description": "ZSTD_cParameter"
      },
      {
        "name": "value",
        "type": "int",
        "required": true,
        "description": "int"
      }
    ]
  },
  {
    "name": "ZSTD_CCtx_getParameter",
    "tool_id": "dll_ZSTD_CCtx_getParameter_19",
    "kind": "export",
    "ordinal": 19,
    "rva": "00009290",
    "confidence": "guaranteed",
    "confidence_factors": {
      "has_signature": true,
      "has_documentation": true,
      "has_parameters": true,
      "has_return_type": true,
      "is_forwarded": true,
      "is_ordinal_only": false
    },
    "signature": {
      "return_type": "size_t",
      "parameters": "const ZSTD_CCtx* cctx, ZSTD_cParameter param, int* value",
      "calling_convention": null,
      "full_prototype": "ZSTDLIB_STATIC_API size_t ZSTD_CCtx_getParameter(const ZSTD_CCtx* cctx, ZSTD_cParameter param, int* value)"
    },
    "documentation": {
      "summary": "! ZSTD_CCtx_getParameter() :\n Get the requested compression parameter value, selected by enum ZSTD_cParameter,\n and store it into int* value.\n@return : 0, or an error code (which can be tested with ZSTD_isError()).",
      "description": "! ZSTD_CCtx_getParameter() :\n Get the requested compression parameter value, selected by enum ZSTD_cParameter,\n and store it into int* value.\n@return : 0, or an error code (which can be tested with ZSTD_isError()).",
      "source_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "source_line": 2364
    },
    "evidence": {
      "discovered_by": "export_analyzer",
      "header_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "forwarded_to": null,
      "demangled_name": null
    },
    "mcp": {
      "version": "1.0",
      "input_schema": {
        "type": "object",
        "properties": {
          "cctx": {
            "type": "string",
            "description": "ZSTD_CCtx*"
          },
          "param": {
            "type": "string",
            "description": "ZSTD_cParameter"
          },
          "value": {
            "type": "string",
            "description": "int*"
          }
        },
        "required": [
          "cctx",
          "param",
          "value"
        ]
      },
      "execution": {
        "method": "dll_import",
        "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
        "function_name": "ZSTD_CCtx_getParameter",
        "calling_convention": "stdcall",
        "charset": "ansi"
      }
    },
    "metadata": {
      "is_signed": false,
      "publisher": null
    },
    "execution": {
      "method": "dll_import",
      "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
      "function_name": "ZSTD_CCtx_getParameter",
      "calling_convention": "stdcall",
      "charset": "ansi"
    },
    "return_type": "size_t",
    "parameters": [
      {
        "name": "cctx",
        "type": "const ZSTD_CCtx*",
        "required": true,
        "description": "const ZSTD_CCtx*"
      },
      {
        "name": "param",
        "type": "ZSTD_cParameter",
        "required": true,
        "description": "ZSTD_cParameter"
      },
      {
        "name": "value",
        "type": "int*",
        "required": true,
        "description": "int*"
      }
    ]
  },
  {
    "name": "ZSTD_CCtx_loadDictionary",
    "tool_id": "dll_ZSTD_CCtx_loadDictionary_20",
    "kind": "export",
    "ordinal": 20,
    "rva": "000098B0",
    "confidence": "guaranteed",
    "confidence_factors": {
      "has_signature": true,
      "has_documentation": true,
      "has_parameters": true,
      "has_return_type": true,
      "is_forwarded": true,
      "is_ordinal_only": false
    },
    "signature": {
      "return_type": "size_t",
      "parameters": "ZSTD_CCtx* cctx, const void* dict, size_t dictSize",
      "calling_convention": null,
      "full_prototype": "ZSTDLIB_API size_t ZSTD_CCtx_loadDictionary(ZSTD_CCtx* cctx, const void* dict, size_t dictSize)"
    },
    "documentation": {
      "summary": "! ZSTD_CCtx_loadDictionary() : Requires v1.4.0+\n Create an internal CDict from `dict` buffer.\n Decompression will have to use same dictionary.\n@result : 0, or an error code (which can be tested with ZSTD_isError()).\n Special: Loading a NULL (or 0-size) dictionary invalidates previous dictionary,\n          meaning \"return to no-dictionary mode\".\n Note 1 : Dictionary is sticky, it will be used for all future compressed frames,\n          until parameters are reset, a new dictionary is loaded, or the dictionary\n          is explicitly invalidated by loading a NULL dictionary.\n Note 2 : Loading a dictionary involves building tables.\n          It's also a CPU consuming operation, with non-negligible impact on latency.\n          Tables are dependent on compression parameters, and for this reason,\n          compression parameters can no longer be changed after loading a dictionary.\n Note 3 :`dict` content will be copied internally.\n          Use experimental ZSTD_CCtx_loadDictionary_byReference() to reference content instead.\n          In such a case, dictionary buffer must outlive its users.\n Note 4 : Use ZSTD_CCtx_loadDictionary_advanced()\n          to precisely select how dictionary content must be interpreted.\n Note 5 : This method does not benefit from LDM (long distance mode).\n          If you want to employ LDM on some large dictionary content,\n          prefer employing ZSTD_CCtx_refPrefix() described below.",
      "description": "! ZSTD_CCtx_loadDictionary() : Requires v1.4.0+\n Create an internal CDict from `dict` buffer.\n Decompression will have to use same dictionary.\n@result : 0, or an error code (which can be tested with ZSTD_isError()).\n Special: Loading a NULL (or 0-size) dictionary invalidates previous dictionary,\n          meaning \"return to no-dictionary mode\".\n Note 1 : Dictionary is sticky, it will be used for all future compressed frames,\n          until parameters are reset, a new dictionary is loaded, or the dictionary\n          is explicitly invalidated by loading a NULL dictionary.\n Note 2 : Loading a dictionary involves building tables.\n          It's also a CPU consuming operation, with non-negligible impact on latency.\n          Tables are dependent on compression parameters, and for this reason,\n          compression parameters can no longer be changed after loading a dictionary.\n Note 3 :`dict` content will be copied internally.\n          Use experimental ZSTD_CCtx_loadDictionary_byReference() to reference content instead.\n          In such a case, dictionary buffer must outlive its users.\n Note 4 : Use ZSTD_CCtx_loadDictionary_advanced()\n          to precisely select how dictionary content must be interpreted.\n Note 5 : This method does not benefit from LDM (long distance mode).\n          If you want to employ LDM on some large dictionary content,\n          prefer employing ZSTD_CCtx_refPrefix() described below.",
      "source_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "source_line": 1108
    },
    "evidence": {
      "discovered_by": "export_analyzer",
      "header_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "forwarded_to": null,
      "demangled_name": null
    },
    "mcp": {
      "version": "1.0",
      "input_schema": {
        "type": "object",
        "properties": {
          "cctx": {
            "type": "string",
            "description": "ZSTD_CCtx*"
          },
          "dict": {
            "type": "string",
            "description": "void*"
          },
          "dictSize": {
            "type": "integer",
            "description": "size_t"
          }
        },
        "required": [
          "cctx",
          "dict",
          "dictSize"
        ]
      },
      "execution": {
        "method": "dll_import",
        "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
        "function_name": "ZSTD_CCtx_loadDictionary",
        "calling_convention": "stdcall",
        "charset": "ansi"
      }
    },
    "metadata": {
      "is_signed": false,
      "publisher": null
    },
    "execution": {
      "method": "dll_import",
      "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
      "function_name": "ZSTD_CCtx_loadDictionary",
      "calling_convention": "stdcall",
      "charset": "ansi"
    },
    "return_type": "size_t",
    "parameters": [
      {
        "name": "cctx",
        "type": "ZSTD_CCtx*",
        "required": true,
        "description": "ZSTD_CCtx*"
      },
      {
        "name": "dict",
        "type": "const void*",
        "required": true,
        "description": "const void*"
      },
      {
        "name": "dictSize",
        "type": "size_t",
        "required": true,
        "description": "size_t"
      }
    ]
  },
  {
    "name": "ZSTD_CCtx_loadDictionary_advanced",
    "tool_id": "dll_ZSTD_CCtx_loadDictionary_advanced_21",
    "kind": "export",
    "ordinal": 21,
    "rva": "000099C0",
    "confidence": "guaranteed",
    "confidence_factors": {
      "has_signature": true,
      "has_documentation": true,
      "has_parameters": true,
      "has_return_type": true,
      "is_forwarded": true,
      "is_ordinal_only": false
    },
    "signature": {
      "return_type": "size_t",
      "parameters": "ZSTD_CCtx* cctx, const void* dict, size_t dictSize, ZSTD_dictLoadMethod_e dictLoadMethod, ZSTD_dictContentType_e dictContentType",
      "calling_convention": null,
      "full_prototype": "ZSTDLIB_STATIC_API size_t ZSTD_CCtx_loadDictionary_advanced(ZSTD_CCtx* cctx, const void* dict, size_t dictSize, ZSTD_dictLoadMethod_e dictLoadMethod, ZSTD_dictContentType_e dictContentType)"
    },
    "documentation": {
      "summary": "! ZSTD_CCtx_loadDictionary_advanced() :\n Same as ZSTD_CCtx_loadDictionary(), but gives finer control over\n how to load the dictionary (by copy ? by reference ?)\n and how to interpret it (automatic ? force raw mode ? full mode only ?)",
      "description": "! ZSTD_CCtx_loadDictionary_advanced() :\n Same as ZSTD_CCtx_loadDictionary(), but gives finer control over\n how to load the dictionary (by copy ? by reference ?)\n and how to interpret it (automatic ? force raw mode ? full mode only ?)",
      "source_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "source_line": 2019
    },
    "evidence": {
      "discovered_by": "export_analyzer",
      "header_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "forwarded_to": null,
      "demangled_name": null
    },
    "mcp": {
      "version": "1.0",
      "input_schema": {
        "type": "object",
        "properties": {
          "cctx": {
            "type": "string",
            "description": "ZSTD_CCtx*"
          },
          "dict": {
            "type": "string",
            "description": "void*"
          },
          "dictSize": {
            "type": "integer",
            "description": "size_t"
          },
          "dictLoadMethod": {
            "type": "string",
            "description": "ZSTD_dictLoadMethod_e"
          },
          "dictContentType": {
            "type": "string",
            "description": "ZSTD_dictContentType_e"
          }
        },
        "required": [
          "cctx",
          "dict",
          "dictSize",
          "dictLoadMethod",
          "dictContentType"
        ]
      },
      "execution": {
        "method": "dll_import",
        "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
        "function_name": "ZSTD_CCtx_loadDictionary_advanced",
        "calling_convention": "stdcall",
        "charset": "ansi"
      }
    },
    "metadata": {
      "is_signed": false,
      "publisher": null
    },
    "execution": {
      "method": "dll_import",
      "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
      "function_name": "ZSTD_CCtx_loadDictionary_advanced",
      "calling_convention": "stdcall",
      "charset": "ansi"
    },
    "return_type": "size_t",
    "parameters": [
      {
        "name": "cctx",
        "type": "ZSTD_CCtx*",
        "required": true,
        "description": "ZSTD_CCtx*"
      },
      {
        "name": "dict",
        "type": "const void*",
        "required": true,
        "description": "const void*"
      },
      {
        "name": "dictSize",
        "type": "size_t",
        "required": true,
        "description": "size_t"
      },
      {
        "name": "dictLoadMethod",
        "type": "ZSTD_dictLoadMethod_e",
        "required": true,
        "description": "ZSTD_dictLoadMethod_e"
      },
      {
        "name": "dictContentType",
        "type": "ZSTD_dictContentType_e",
        "required": true,
        "description": "ZSTD_dictContentType_e"
      }
    ]
  },
  {
    "name": "ZSTD_CCtx_loadDictionary_byReference",
    "tool_id": "dll_ZSTD_CCtx_loadDictionary_byReference_22",
    "kind": "export",
    "ordinal": 22,
    "rva": "00009AA0",
    "confidence": "guaranteed",
    "confidence_factors": {
      "has_signature": true,
      "has_documentation": true,
      "has_parameters": true,
      "has_return_type": true,
      "is_forwarded": true,
      "is_ordinal_only": false
    },
    "signature": {
      "return_type": "size_t",
      "parameters": "ZSTD_CCtx* cctx, const void* dict, size_t dictSize",
      "calling_convention": null,
      "full_prototype": "ZSTDLIB_STATIC_API size_t ZSTD_CCtx_loadDictionary_byReference(ZSTD_CCtx* cctx, const void* dict, size_t dictSize)"
    },
    "documentation": {
      "summary": "! ZSTD_CCtx_loadDictionary_byReference() :\n Same as ZSTD_CCtx_loadDictionary(), but dictionary content is referenced, instead of being copied into CCtx.\n It saves some memory, but also requires that `dict` outlives its usage within `cctx`",
      "description": "! ZSTD_CCtx_loadDictionary_byReference() :\n Same as ZSTD_CCtx_loadDictionary(), but dictionary content is referenced, instead of being copied into CCtx.\n It saves some memory, but also requires that `dict` outlives its usage within `cctx`",
      "source_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "source_line": 2013
    },
    "evidence": {
      "discovered_by": "export_analyzer",
      "header_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "forwarded_to": null,
      "demangled_name": null
    },
    "mcp": {
      "version": "1.0",
      "input_schema": {
        "type": "object",
        "properties": {
          "cctx": {
            "type": "string",
            "description": "ZSTD_CCtx*"
          },
          "dict": {
            "type": "string",
            "description": "void*"
          },
          "dictSize": {
            "type": "integer",
            "description": "size_t"
          }
        },
        "required": [
          "cctx",
          "dict",
          "dictSize"
        ]
      },
      "execution": {
        "method": "dll_import",
        "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
        "function_name": "ZSTD_CCtx_loadDictionary_byReference",
        "calling_convention": "stdcall",
        "charset": "ansi"
      }
    },
    "metadata": {
      "is_signed": false,
      "publisher": null
    },
    "execution": {
      "method": "dll_import",
      "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
      "function_name": "ZSTD_CCtx_loadDictionary_byReference",
      "calling_convention": "stdcall",
      "charset": "ansi"
    },
    "return_type": "size_t",
    "parameters": [
      {
        "name": "cctx",
        "type": "ZSTD_CCtx*",
        "required": true,
        "description": "ZSTD_CCtx*"
      },
      {
        "name": "dict",
        "type": "const void*",
        "required": true,
        "description": "const void*"
      },
      {
        "name": "dictSize",
        "type": "size_t",
        "required": true,
        "description": "size_t"
      }
    ]
  },
  {
    "name": "ZSTD_CCtx_refCDict",
    "tool_id": "dll_ZSTD_CCtx_refCDict_23",
    "kind": "export",
    "ordinal": 23,
    "rva": "00009B20",
    "confidence": "guaranteed",
    "confidence_factors": {
      "has_signature": true,
      "has_documentation": true,
      "has_parameters": true,
      "has_return_type": true,
      "is_forwarded": true,
      "is_ordinal_only": false
    },
    "signature": {
      "return_type": "size_t",
      "parameters": "ZSTD_CCtx* cctx, const ZSTD_CDict* cdict",
      "calling_convention": null,
      "full_prototype": "ZSTDLIB_API size_t ZSTD_CCtx_refCDict(ZSTD_CCtx* cctx, const ZSTD_CDict* cdict)"
    },
    "documentation": {
      "summary": "! ZSTD_CCtx_refCDict() : Requires v1.4.0+\n Reference a prepared dictionary, to be used for all future compressed frames.\n Note that compression parameters are enforced from within CDict,\n and supersede any compression parameter previously set within CCtx.\n The parameters ignored are labelled as \"superseded-by-cdict\" in the ZSTD_cParameter enum docs.\n The ignored parameters will be used again if the CCtx is returned to no-dictionary mode.\n The dictionary will remain valid for future compressed frames using same CCtx.\n@result : 0, or an error code (which can be tested with ZSTD_isError()).\n Special : Referencing a NULL CDict means \"return to no-dictionary mode\".\n Note 1 : Currently, only one dictionary can be managed.\n          Referencing a new dictionary effectively \"discards\" any previous one.\n Note 2 : CDict is just referenced, its lifetime must outlive its usage within CCtx.",
      "description": "! ZSTD_CCtx_refCDict() : Requires v1.4.0+\n Reference a prepared dictionary, to be used for all future compressed frames.\n Note that compression parameters are enforced from within CDict,\n and supersede any compression parameter previously set within CCtx.\n The parameters ignored are labelled as \"superseded-by-cdict\" in the ZSTD_cParameter enum docs.\n The ignored parameters will be used again if the CCtx is returned to no-dictionary mode.\n The dictionary will remain valid for future compressed frames using same CCtx.\n@result : 0, or an error code (which can be tested with ZSTD_isError()).\n Special : Referencing a NULL CDict means \"return to no-dictionary mode\".\n Note 1 : Currently, only one dictionary can be managed.\n          Referencing a new dictionary effectively \"discards\" any previous one.\n Note 2 : CDict is just referenced, its lifetime must outlive its usage within CCtx.",
      "source_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "source_line": 1122
    },
    "evidence": {
      "discovered_by": "export_analyzer",
      "header_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "forwarded_to": null,
      "demangled_name": null
    },
    "mcp": {
      "version": "1.0",
      "input_schema": {
        "type": "object",
        "properties": {
          "cctx": {
            "type": "string",
            "description": "ZSTD_CCtx*"
          },
          "cdict": {
            "type": "string",
            "description": "ZSTD_CDict*"
          }
        },
        "required": [
          "cctx",
          "cdict"
        ]
      },
      "execution": {
        "method": "dll_import",
        "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
        "function_name": "ZSTD_CCtx_refCDict",
        "calling_convention": "stdcall",
        "charset": "ansi"
      }
    },
    "metadata": {
      "is_signed": false,
      "publisher": null
    },
    "execution": {
      "method": "dll_import",
      "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
      "function_name": "ZSTD_CCtx_refCDict",
      "calling_convention": "stdcall",
      "charset": "ansi"
    },
    "return_type": "size_t",
    "parameters": [
      {
        "name": "cctx",
        "type": "ZSTD_CCtx*",
        "required": true,
        "description": "ZSTD_CCtx*"
      },
      {
        "name": "cdict",
        "type": "const ZSTD_CDict*",
        "required": true,
        "description": "const ZSTD_CDict*"
      }
    ]
  },
  {
    "name": "ZSTD_CCtx_refPrefix",
    "tool_id": "dll_ZSTD_CCtx_refPrefix_24",
    "kind": "export",
    "ordinal": 24,
    "rva": "00009B70",
    "confidence": "guaranteed",
    "confidence_factors": {
      "has_signature": true,
      "has_documentation": true,
      "has_parameters": true,
      "has_return_type": true,
      "is_forwarded": true,
      "is_ordinal_only": false
    },
    "signature": {
      "return_type": "size_t",
      "parameters": "ZSTD_CCtx* cctx, const void* prefix, size_t prefixSize",
      "calling_convention": null,
      "full_prototype": "ZSTDLIB_API size_t ZSTD_CCtx_refPrefix(ZSTD_CCtx* cctx, const void* prefix, size_t prefixSize)"
    },
    "documentation": {
      "summary": "! ZSTD_CCtx_refPrefix() : Requires v1.4.0+\n Reference a prefix (single-usage dictionary) for next compressed frame.\n A prefix is **only used once**. Tables are discarded at end of frame (ZSTD_e_end).\n Decompression will need same prefix to properly regenerate data.\n Compressing with a prefix is similar in outcome as performing a diff and compressing it,\n but performs much faster, especially during decompression (compression speed is tunable with compression level).\n This method is compatible with LDM (long distance mode).\n@result : 0, or an error code (which can be tested with ZSTD_isError()).\n Special: Adding any prefix (including NULL) invalidates any previous prefix or dictionary\n Note 1 : Prefix buffer is referenced. It **must** outlive compression.\n          Its content must remain unmodified during compression.\n Note 2 : If the intention is to diff some large src data blob with some prior version of itself,\n          ensure that the window size is large enough to contain the entire source.\n          See ZSTD_c_windowLog.\n Note 3 : Referencing a prefix involves building tables, which are dependent on compression parameters.\n          It's a CPU consuming operation, with non-negligible impact on latency.\n          If there is a need to use the same prefix multiple times, consider loadDictionary instead.\n Note 4 : By default, the prefix is interpreted as raw content (ZSTD_dct_rawContent).\n          Use experimental ZSTD_CCtx_refPrefix_advanced() to alter dictionary interpretation.",
      "description": "! ZSTD_CCtx_refPrefix() : Requires v1.4.0+\n Reference a prefix (single-usage dictionary) for next compressed frame.\n A prefix is **only used once**. Tables are discarded at end of frame (ZSTD_e_end).\n Decompression will need same prefix to properly regenerate data.\n Compressing with a prefix is similar in outcome as performing a diff and compressing it,\n but performs much faster, especially during decompression (compression speed is tunable with compression level).\n This method is compatible with LDM (long distance mode).\n@result : 0, or an error code (which can be tested with ZSTD_isError()).\n Special: Adding any prefix (including NULL) invalidates any previous prefix or dictionary\n Note 1 : Prefix buffer is referenced. It **must** outlive compression.\n          Its content must remain unmodified during compression.\n Note 2 : If the intention is to diff some large src data blob with some prior version of itself,\n          ensure that the window size is large enough to contain the entire source.\n          See ZSTD_c_windowLog.\n Note 3 : Referencing a prefix involves building tables, which are dependent on compression parameters.\n          It's a CPU consuming operation, with non-negligible impact on latency.\n          If there is a need to use the same prefix multiple times, consider loadDictionary instead.\n Note 4 : By default, the prefix is interpreted as raw content (ZSTD_dct_rawContent).\n          Use experimental ZSTD_CCtx_refPrefix_advanced() to alter dictionary interpretation.",
      "source_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "source_line": 1143
    },
    "evidence": {
      "discovered_by": "export_analyzer",
      "header_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "forwarded_to": null,
      "demangled_name": null
    },
    "mcp": {
      "version": "1.0",
      "input_schema": {
        "type": "object",
        "properties": {
          "cctx": {
            "type": "string",
            "description": "ZSTD_CCtx*"
          },
          "prefix": {
            "type": "string",
            "description": "void*"
          },
          "prefixSize": {
            "type": "integer",
            "description": "size_t"
          }
        },
        "required": [
          "cctx",
          "prefix",
          "prefixSize"
        ]
      },
      "execution": {
        "method": "dll_import",
        "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
        "function_name": "ZSTD_CCtx_refPrefix",
        "calling_convention": "stdcall",
        "charset": "ansi"
      }
    },
    "metadata": {
      "is_signed": false,
      "publisher": null
    },
    "execution": {
      "method": "dll_import",
      "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
      "function_name": "ZSTD_CCtx_refPrefix",
      "calling_convention": "stdcall",
      "charset": "ansi"
    },
    "return_type": "size_t",
    "parameters": [
      {
        "name": "cctx",
        "type": "ZSTD_CCtx*",
        "required": true,
        "description": "ZSTD_CCtx*"
      },
      {
        "name": "prefix",
        "type": "const void*",
        "required": true,
        "description": "const void*"
      },
      {
        "name": "prefixSize",
        "type": "size_t",
        "required": true,
        "description": "size_t"
      }
    ]
  },
  {
    "name": "ZSTD_CCtx_refPrefix_advanced",
    "tool_id": "dll_ZSTD_CCtx_refPrefix_advanced_25",
    "kind": "export",
    "ordinal": 25,
    "rva": "00009BF0",
    "confidence": "guaranteed",
    "confidence_factors": {
      "has_signature": true,
      "has_documentation": true,
      "has_parameters": true,
      "has_return_type": true,
      "is_forwarded": true,
      "is_ordinal_only": false
    },
    "signature": {
      "return_type": "size_t",
      "parameters": "ZSTD_CCtx* cctx, const void* prefix, size_t prefixSize, ZSTD_dictContentType_e dictContentType",
      "calling_convention": null,
      "full_prototype": "ZSTDLIB_STATIC_API size_t ZSTD_CCtx_refPrefix_advanced(ZSTD_CCtx* cctx, const void* prefix, size_t prefixSize, ZSTD_dictContentType_e dictContentType)"
    },
    "documentation": {
      "summary": "! ZSTD_CCtx_refPrefix_advanced() :\n Same as ZSTD_CCtx_refPrefix(), but gives finer control over\n how to interpret prefix content (automatic ? force raw mode (default) ? full mode only ?)",
      "description": "! ZSTD_CCtx_refPrefix_advanced() :\n Same as ZSTD_CCtx_refPrefix(), but gives finer control over\n how to interpret prefix content (automatic ? force raw mode (default) ? full mode only ?)",
      "source_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "source_line": 2024
    },
    "evidence": {
      "discovered_by": "export_analyzer",
      "header_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "forwarded_to": null,
      "demangled_name": null
    },
    "mcp": {
      "version": "1.0",
      "input_schema": {
        "type": "object",
        "properties": {
          "cctx": {
            "type": "string",
            "description": "ZSTD_CCtx*"
          },
          "prefix": {
            "type": "string",
            "description": "void*"
          },
          "prefixSize": {
            "type": "integer",
            "description": "size_t"
          },
          "dictContentType": {
            "type": "string",
            "description": "ZSTD_dictContentType_e"
          }
        },
        "required": [
          "cctx",
          "prefix",
          "prefixSize",
          "dictContentType"
        ]
      },
      "execution": {
        "method": "dll_import",
        "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
        "function_name": "ZSTD_CCtx_refPrefix_advanced",
        "calling_convention": "stdcall",
        "charset": "ansi"
      }
    },
    "metadata": {
      "is_signed": false,
      "publisher": null
    },
    "execution": {
      "method": "dll_import",
      "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
      "function_name": "ZSTD_CCtx_refPrefix_advanced",
      "calling_convention": "stdcall",
      "charset": "ansi"
    },
    "return_type": "size_t",
    "parameters": [
      {
        "name": "cctx",
        "type": "ZSTD_CCtx*",
        "required": true,
        "description": "ZSTD_CCtx*"
      },
      {
        "name": "prefix",
        "type": "const void*",
        "required": true,
        "description": "const void*"
      },
      {
        "name": "prefixSize",
        "type": "size_t",
        "required": true,
        "description": "size_t"
      },
      {
        "name": "dictContentType",
        "type": "ZSTD_dictContentType_e",
        "required": true,
        "description": "ZSTD_dictContentType_e"
      }
    ]
  },
  {
    "name": "ZSTD_CCtx_refThreadPool",
    "tool_id": "dll_ZSTD_CCtx_refThreadPool_26",
    "kind": "export",
    "ordinal": 26,
    "rva": "00009C60",
    "confidence": "guaranteed",
    "confidence_factors": {
      "has_signature": true,
      "has_documentation": false,
      "has_parameters": true,
      "has_return_type": true,
      "is_forwarded": true,
      "is_ordinal_only": false
    },
    "signature": {
      "return_type": "size_t",
      "parameters": "ZSTD_CCtx* cctx, ZSTD_threadPool* pool",
      "calling_convention": null,
      "full_prototype": "ZSTDLIB_STATIC_API size_t ZSTD_CCtx_refThreadPool(ZSTD_CCtx* cctx, ZSTD_threadPool* pool)"
    },
    "documentation": {
      "summary": "",
      "description": "",
      "source_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "source_line": 1909
    },
    "evidence": {
      "discovered_by": "export_analyzer",
      "header_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "forwarded_to": null,
      "demangled_name": null
    },
    "mcp": {
      "version": "1.0",
      "input_schema": {
        "type": "object",
        "properties": {
          "cctx": {
            "type": "string",
            "description": "ZSTD_CCtx*"
          },
          "pool": {
            "type": "string",
            "description": "ZSTD_threadPool*"
          }
        },
        "required": [
          "cctx",
          "pool"
        ]
      },
      "execution": {
        "method": "dll_import",
        "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
        "function_name": "ZSTD_CCtx_refThreadPool",
        "calling_convention": "stdcall",
        "charset": "ansi"
      }
    },
    "metadata": {
      "is_signed": false,
      "publisher": null
    },
    "execution": {
      "method": "dll_import",
      "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
      "function_name": "ZSTD_CCtx_refThreadPool",
      "calling_convention": "stdcall",
      "charset": "ansi"
    },
    "return_type": "size_t",
    "parameters": [
      {
        "name": "cctx",
        "type": "ZSTD_CCtx*",
        "required": true,
        "description": "ZSTD_CCtx*"
      },
      {
        "name": "pool",
        "type": "ZSTD_threadPool*",
        "required": true,
        "description": "ZSTD_threadPool*"
      }
    ]
  },
  {
    "name": "ZSTD_CCtx_reset",
    "tool_id": "dll_ZSTD_CCtx_reset_27",
    "kind": "export",
    "ordinal": 27,
    "rva": "00009C80",
    "confidence": "guaranteed",
    "confidence_factors": {
      "has_signature": true,
      "has_documentation": true,
      "has_parameters": true,
      "has_return_type": true,
      "is_forwarded": true,
      "is_ordinal_only": false
    },
    "signature": {
      "return_type": "size_t",
      "parameters": "ZSTD_CCtx* cctx, ZSTD_ResetDirective reset",
      "calling_convention": null,
      "full_prototype": "ZSTDLIB_API size_t ZSTD_CCtx_reset(ZSTD_CCtx* cctx, ZSTD_ResetDirective reset)"
    },
    "documentation": {
      "summary": "! ZSTD_CCtx_reset() :\n There are 2 different things that can be reset, independently or jointly :\n - The session : will stop compressing current frame, and make CCtx ready to start a new one.\n                 Useful after an error, or to interrupt any ongoing compression.\n                 Any internal data not yet flushed is cancelled.\n                 Compression parameters and dictionary remain unchanged.\n                 They will be used to compress next frame.\n                 Resetting session never fails.\n - The parameters : changes all parameters back to \"default\".\n                 This also removes any reference to any dictionary or external sequence producer.\n                 Parameters can only be changed between 2 sessions (i.e. no compression is currently ongoing)\n                 otherwise the reset fails, and function returns an error value (which can be tested using ZSTD_isError())\n - Both : similar to resetting the session, followed by resetting parameters.",
      "description": "! ZSTD_CCtx_reset() :\n There are 2 different things that can be reset, independently or jointly :\n - The session : will stop compressing current frame, and make CCtx ready to start a new one.\n                 Useful after an error, or to interrupt any ongoing compression.\n                 Any internal data not yet flushed is cancelled.\n                 Compression parameters and dictionary remain unchanged.\n                 They will be used to compress next frame.\n                 Resetting session never fails.\n - The parameters : changes all parameters back to \"default\".\n                 This also removes any reference to any dictionary or external sequence producer.\n                 Parameters can only be changed between 2 sessions (i.e. no compression is currently ongoing)\n                 otherwise the reset fails, and function returns an error value (which can be tested using ZSTD_isError())\n - Both : similar to resetting the session, followed by resetting parameters.",
      "source_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "source_line": 609
    },
    "evidence": {
      "discovered_by": "export_analyzer",
      "header_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "forwarded_to": null,
      "demangled_name": null
    },
    "mcp": {
      "version": "1.0",
      "input_schema": {
        "type": "object",
        "properties": {
          "cctx": {
            "type": "string",
            "description": "ZSTD_CCtx*"
          },
          "reset": {
            "type": "string",
            "description": "ZSTD_ResetDirective"
          }
        },
        "required": [
          "cctx",
          "reset"
        ]
      },
      "execution": {
        "method": "dll_import",
        "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
        "function_name": "ZSTD_CCtx_reset",
        "calling_convention": "stdcall",
        "charset": "ansi"
      }
    },
    "metadata": {
      "is_signed": false,
      "publisher": null
    },
    "execution": {
      "method": "dll_import",
      "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
      "function_name": "ZSTD_CCtx_reset",
      "calling_convention": "stdcall",
      "charset": "ansi"
    },
    "return_type": "size_t",
    "parameters": [
      {
        "name": "cctx",
        "type": "ZSTD_CCtx*",
        "required": true,
        "description": "ZSTD_CCtx*"
      },
      {
        "name": "reset",
        "type": "ZSTD_ResetDirective",
        "required": true,
        "description": "ZSTD_ResetDirective"
      }
    ]
  },
  {
    "name": "ZSTD_CCtx_setCParams",
    "tool_id": "dll_ZSTD_CCtx_setCParams_28",
    "kind": "export",
    "ordinal": 28,
    "rva": "00009D30",
    "confidence": "guaranteed",
    "confidence_factors": {
      "has_signature": true,
      "has_documentation": true,
      "has_parameters": true,
      "has_return_type": true,
      "is_forwarded": true,
      "is_ordinal_only": false
    },
    "signature": {
      "return_type": "size_t",
      "parameters": "ZSTD_CCtx* cctx, ZSTD_compressionParameters cparams",
      "calling_convention": null,
      "full_prototype": "ZSTDLIB_STATIC_API size_t ZSTD_CCtx_setCParams(ZSTD_CCtx* cctx, ZSTD_compressionParameters cparams)"
    },
    "documentation": {
      "summary": "! ZSTD_CCtx_setCParams() :\n Set all parameters provided within @p cparams into the working @p cctx.\n Note : if modifying parameters during compression (MT mode only),\n        note that changes to the .windowLog parameter will be ignored.\n@return 0 on success, or an error code (can be checked with ZSTD_isError()).\n        On failure, no parameters are updated.",
      "description": "! ZSTD_CCtx_setCParams() :\n Set all parameters provided within @p cparams into the working @p cctx.\n Note : if modifying parameters during compression (MT mode only),\n        note that changes to the .windowLog parameter will be ignored.\n@return 0 on success, or an error code (can be checked with ZSTD_isError()).\n        On failure, no parameters are updated.",
      "source_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "source_line": 1971
    },
    "evidence": {
      "discovered_by": "export_analyzer",
      "header_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "forwarded_to": null,
      "demangled_name": null
    },
    "mcp": {
      "version": "1.0",
      "input_schema": {
        "type": "object",
        "properties": {
          "cctx": {
            "type": "string",
            "description": "ZSTD_CCtx*"
          },
          "cparams": {
            "type": "string",
            "description": "ZSTD_compressionParameters"
          }
        },
        "required": [
          "cctx",
          "cparams"
        ]
      },
      "execution": {
        "method": "dll_import",
        "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
        "function_name": "ZSTD_CCtx_setCParams",
        "calling_convention": "stdcall",
        "charset": "ansi"
      }
    },
    "metadata": {
      "is_signed": false,
      "publisher": null
    },
    "execution": {
      "method": "dll_import",
      "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
      "function_name": "ZSTD_CCtx_setCParams",
      "calling_convention": "stdcall",
      "charset": "ansi"
    },
    "return_type": "size_t",
    "parameters": [
      {
        "name": "cctx",
        "type": "ZSTD_CCtx*",
        "required": true,
        "description": "ZSTD_CCtx*"
      },
      {
        "name": "cparams",
        "type": "ZSTD_compressionParameters",
        "required": true,
        "description": "ZSTD_compressionParameters"
      }
    ]
  },
  {
    "name": "ZSTD_CCtx_setFParams",
    "tool_id": "dll_ZSTD_CCtx_setFParams_29",
    "kind": "export",
    "ordinal": 29,
    "rva": "00009FD0",
    "confidence": "guaranteed",
    "confidence_factors": {
      "has_signature": true,
      "has_documentation": true,
      "has_parameters": true,
      "has_return_type": true,
      "is_forwarded": true,
      "is_ordinal_only": false
    },
    "signature": {
      "return_type": "size_t",
      "parameters": "ZSTD_CCtx* cctx, ZSTD_frameParameters fparams",
      "calling_convention": null,
      "full_prototype": "ZSTDLIB_STATIC_API size_t ZSTD_CCtx_setFParams(ZSTD_CCtx* cctx, ZSTD_frameParameters fparams)"
    },
    "documentation": {
      "summary": "! ZSTD_CCtx_setFParams() :\n Set all parameters provided within @p fparams into the working @p cctx.\n@return 0 on success, or an error code (can be checked with ZSTD_isError()).",
      "description": "! ZSTD_CCtx_setFParams() :\n Set all parameters provided within @p fparams into the working @p cctx.\n@return 0 on success, or an error code (can be checked with ZSTD_isError()).",
      "source_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "source_line": 1977
    },
    "evidence": {
      "discovered_by": "export_analyzer",
      "header_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "forwarded_to": null,
      "demangled_name": null
    },
    "mcp": {
      "version": "1.0",
      "input_schema": {
        "type": "object",
        "properties": {
          "cctx": {
            "type": "string",
            "description": "ZSTD_CCtx*"
          },
          "fparams": {
            "type": "string",
            "description": "ZSTD_frameParameters"
          }
        },
        "required": [
          "cctx",
          "fparams"
        ]
      },
      "execution": {
        "method": "dll_import",
        "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
        "function_name": "ZSTD_CCtx_setFParams",
        "calling_convention": "stdcall",
        "charset": "ansi"
      }
    },
    "metadata": {
      "is_signed": false,
      "publisher": null
    },
    "execution": {
      "method": "dll_import",
      "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
      "function_name": "ZSTD_CCtx_setFParams",
      "calling_convention": "stdcall",
      "charset": "ansi"
    },
    "return_type": "size_t",
    "parameters": [
      {
        "name": "cctx",
        "type": "ZSTD_CCtx*",
        "required": true,
        "description": "ZSTD_CCtx*"
      },
      {
        "name": "fparams",
        "type": "ZSTD_frameParameters",
        "required": true,
        "description": "ZSTD_frameParameters"
      }
    ]
  },
  {
    "name": "ZSTD_CCtx_setParameter",
    "tool_id": "dll_ZSTD_CCtx_setParameter_30",
    "kind": "export",
    "ordinal": 30,
    "rva": "0000A080",
    "confidence": "guaranteed",
    "confidence_factors": {
      "has_signature": true,
      "has_documentation": true,
      "has_parameters": true,
      "has_return_type": true,
      "is_forwarded": true,
      "is_ordinal_only": false
    },
    "signature": {
      "return_type": "size_t",
      "parameters": "ZSTD_CCtx* cctx, ZSTD_cParameter param, int value",
      "calling_convention": null,
      "full_prototype": "ZSTDLIB_API size_t ZSTD_CCtx_setParameter(ZSTD_CCtx* cctx, ZSTD_cParameter param, int value)"
    },
    "documentation": {
      "summary": "! ZSTD_CCtx_setParameter() :\n Set one compression parameter, selected by enum ZSTD_cParameter.\n All parameters have valid bounds. Bounds can be queried using ZSTD_cParam_getBounds().\n Providing a value beyond bound will either clamp it, or trigger an error (depending on parameter).\n Setting a parameter is generally only possible during frame initialization (before starting compression).\n Exception : when using multi-threading mode (nbWorkers >= 1),\n             the following parameters can be updated _during_ compression (within same frame):\n             => compressionLevel, hashLog, chainLog, searchLog, minMatch, targetLength and strategy.\n             new parameters will be active for next job only (after a flush()).\n@return : an error code (which can be tested using ZSTD_isError()).",
      "description": "! ZSTD_CCtx_setParameter() :\n Set one compression parameter, selected by enum ZSTD_cParameter.\n All parameters have valid bounds. Bounds can be queried using ZSTD_cParam_getBounds().\n Providing a value beyond bound will either clamp it, or trigger an error (depending on parameter).\n Setting a parameter is generally only possible during frame initialization (before starting compression).\n Exception : when using multi-threading mode (nbWorkers >= 1),\n             the following parameters can be updated _during_ compression (within same frame):\n             => compressionLevel, hashLog, chainLog, searchLog, minMatch, targetLength and strategy.\n             new parameters will be active for next job only (after a flush()).\n@return : an error code (which can be tested using ZSTD_isError()).",
      "source_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "source_line": 570
    },
    "evidence": {
      "discovered_by": "export_analyzer",
      "header_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "forwarded_to": null,
      "demangled_name": null
    },
    "mcp": {
      "version": "1.0",
      "input_schema": {
        "type": "object",
        "properties": {
          "cctx": {
            "type": "string",
            "description": "ZSTD_CCtx*"
          },
          "param": {
            "type": "string",
            "description": "ZSTD_cParameter"
          },
          "value": {
            "type": "integer",
            "description": "int"
          }
        },
        "required": [
          "cctx",
          "param",
          "value"
        ]
      },
      "execution": {
        "method": "dll_import",
        "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
        "function_name": "ZSTD_CCtx_setParameter",
        "calling_convention": "stdcall",
        "charset": "ansi"
      }
    },
    "metadata": {
      "is_signed": false,
      "publisher": null
    },
    "execution": {
      "method": "dll_import",
      "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
      "function_name": "ZSTD_CCtx_setParameter",
      "calling_convention": "stdcall",
      "charset": "ansi"
    },
    "return_type": "size_t",
    "parameters": [
      {
        "name": "cctx",
        "type": "ZSTD_CCtx*",
        "required": true,
        "description": "ZSTD_CCtx*"
      },
      {
        "name": "param",
        "type": "ZSTD_cParameter",
        "required": true,
        "description": "ZSTD_cParameter"
      },
      {
        "name": "value",
        "type": "int",
        "required": true,
        "description": "int"
      }
    ]
  },
  {
    "name": "ZSTD_CCtx_setParametersUsingCCtxParams",
    "tool_id": "dll_ZSTD_CCtx_setParametersUsingCCtxParams_31",
    "kind": "export",
    "ordinal": 31,
    "rva": "0000A370",
    "confidence": "guaranteed",
    "confidence_factors": {
      "has_signature": true,
      "has_documentation": true,
      "has_parameters": true,
      "has_return_type": true,
      "is_forwarded": true,
      "is_ordinal_only": false
    },
    "signature": {
      "return_type": "size_t",
      "parameters": "ZSTD_CCtx* cctx, const ZSTD_CCtx_params* params",
      "calling_convention": null,
      "full_prototype": "ZSTDLIB_STATIC_API size_t ZSTD_CCtx_setParametersUsingCCtxParams( ZSTD_CCtx* cctx, const ZSTD_CCtx_params* params)"
    },
    "documentation": {
      "summary": "! ZSTD_CCtx_setParametersUsingCCtxParams() :\n Apply a set of ZSTD_CCtx_params to the compression context.\n This can be done even after compression is started,\n   if nbWorkers==0, this will have no impact until a new compression is started.\n   if nbWorkers>=1, new parameters will be picked up at next job,\n      with a few restrictions (windowLog, pledgedSrcSize, nbWorkers, jobSize, and overlapLog are not updated).",
      "description": "! ZSTD_CCtx_setParametersUsingCCtxParams() :\n Apply a set of ZSTD_CCtx_params to the compression context.\n This can be done even after compression is started,\n   if nbWorkers==0, this will have no impact until a new compression is started.\n   if nbWorkers>=1, new parameters will be picked up at next job,\n      with a few restrictions (windowLog, pledgedSrcSize, nbWorkers, jobSize, and overlapLog are not updated).",
      "source_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "source_line": 2428
    },
    "evidence": {
      "discovered_by": "export_analyzer",
      "header_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "forwarded_to": null,
      "demangled_name": null
    },
    "mcp": {
      "version": "1.0",
      "input_schema": {
        "type": "object",
        "properties": {
          "cctx": {
            "type": "string",
            "description": "ZSTD_CCtx*"
          },
          "params": {
            "type": "string",
            "description": "ZSTD_CCtx_params*"
          }
        },
        "required": [
          "cctx",
          "params"
        ]
      },
      "execution": {
        "method": "dll_import",
        "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
        "function_name": "ZSTD_CCtx_setParametersUsingCCtxParams",
        "calling_convention": "stdcall",
        "charset": "ansi"
      }
    },
    "metadata": {
      "is_signed": false,
      "publisher": null
    },
    "execution": {
      "method": "dll_import",
      "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
      "function_name": "ZSTD_CCtx_setParametersUsingCCtxParams",
      "calling_convention": "stdcall",
      "charset": "ansi"
    },
    "return_type": "size_t",
    "parameters": [
      {
        "name": "cctx",
        "type": "ZSTD_CCtx*",
        "required": true,
        "description": "ZSTD_CCtx*"
      },
      {
        "name": "params",
        "type": "const ZSTD_CCtx_params*",
        "required": true,
        "description": "const ZSTD_CCtx_params*"
      }
    ]
  },
  {
    "name": "ZSTD_CCtx_setParams",
    "tool_id": "dll_ZSTD_CCtx_setParams_32",
    "kind": "export",
    "ordinal": 32,
    "rva": "0000A410",
    "confidence": "guaranteed",
    "confidence_factors": {
      "has_signature": true,
      "has_documentation": true,
      "has_parameters": true,
      "has_return_type": true,
      "is_forwarded": true,
      "is_ordinal_only": false
    },
    "signature": {
      "return_type": "size_t",
      "parameters": "ZSTD_CCtx* cctx, ZSTD_parameters params",
      "calling_convention": null,
      "full_prototype": "ZSTDLIB_STATIC_API size_t ZSTD_CCtx_setParams(ZSTD_CCtx* cctx, ZSTD_parameters params)"
    },
    "documentation": {
      "summary": "! ZSTD_CCtx_setParams() :\n Set all parameters provided within @p params into the working @p cctx.\n@return 0 on success, or an error code (can be checked with ZSTD_isError()).",
      "description": "! ZSTD_CCtx_setParams() :\n Set all parameters provided within @p params into the working @p cctx.\n@return 0 on success, or an error code (can be checked with ZSTD_isError()).",
      "source_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "source_line": 1983
    },
    "evidence": {
      "discovered_by": "export_analyzer",
      "header_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "forwarded_to": null,
      "demangled_name": null
    },
    "mcp": {
      "version": "1.0",
      "input_schema": {
        "type": "object",
        "properties": {
          "cctx": {
            "type": "string",
            "description": "ZSTD_CCtx*"
          },
          "params": {
            "type": "string",
            "description": "ZSTD_parameters"
          }
        },
        "required": [
          "cctx",
          "params"
        ]
      },
      "execution": {
        "method": "dll_import",
        "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
        "function_name": "ZSTD_CCtx_setParams",
        "calling_convention": "stdcall",
        "charset": "ansi"
      }
    },
    "metadata": {
      "is_signed": false,
      "publisher": null
    },
    "execution": {
      "method": "dll_import",
      "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
      "function_name": "ZSTD_CCtx_setParams",
      "calling_convention": "stdcall",
      "charset": "ansi"
    },
    "return_type": "size_t",
    "parameters": [
      {
        "name": "cctx",
        "type": "ZSTD_CCtx*",
        "required": true,
        "description": "ZSTD_CCtx*"
      },
      {
        "name": "params",
        "type": "ZSTD_parameters",
        "required": true,
        "description": "ZSTD_parameters"
      }
    ]
  },
  {
    "name": "ZSTD_CCtx_setPledgedSrcSize",
    "tool_id": "dll_ZSTD_CCtx_setPledgedSrcSize_33",
    "kind": "export",
    "ordinal": 33,
    "rva": "0000A5B0",
    "confidence": "guaranteed",
    "confidence_factors": {
      "has_signature": true,
      "has_documentation": true,
      "has_parameters": true,
      "has_return_type": true,
      "is_forwarded": true,
      "is_ordinal_only": false
    },
    "signature": {
      "return_type": "size_t",
      "parameters": "ZSTD_CCtx* cctx, unsigned long long pledgedSrcSize",
      "calling_convention": null,
      "full_prototype": "ZSTDLIB_API size_t ZSTD_CCtx_setPledgedSrcSize(ZSTD_CCtx* cctx, unsigned long long pledgedSrcSize)"
    },
    "documentation": {
      "summary": "! ZSTD_CCtx_setPledgedSrcSize() :\n Total input data size to be compressed as a single frame.\n Value will be written in frame header, unless if explicitly forbidden using ZSTD_c_contentSizeFlag.\n This value will also be controlled at end of frame, and trigger an error if not respected.\n@result : 0, or an error code (which can be tested with ZSTD_isError()).\n Note 1 : pledgedSrcSize==0 actually means zero, aka an empty frame.\n          In order to mean \"unknown content size\", pass constant ZSTD_CONTENTSIZE_UNKNOWN.\n          ZSTD_CONTENTSIZE_UNKNOWN is default value for any new frame.\n Note 2 : pledgedSrcSize is only valid once, for the next frame.\n          It's discarded at the end of the frame, and replaced by ZSTD_CONTENTSIZE_UNKNOWN.\n Note 3 : Whenever all input data is provided and consumed in a single round,\n          for example with ZSTD_compress2(),\n          or invoking immediately ZSTD_compressStream2(,,,ZSTD_e_end),\n          this value is automatically overridden by srcSize instead.",
      "description": "! ZSTD_CCtx_setPledgedSrcSize() :\n Total input data size to be compressed as a single frame.\n Value will be written in frame header, unless if explicitly forbidden using ZSTD_c_contentSizeFlag.\n This value will also be controlled at end of frame, and trigger an error if not respected.\n@result : 0, or an error code (which can be tested with ZSTD_isError()).\n Note 1 : pledgedSrcSize==0 actually means zero, aka an empty frame.\n          In order to mean \"unknown content size\", pass constant ZSTD_CONTENTSIZE_UNKNOWN.\n          ZSTD_CONTENTSIZE_UNKNOWN is default value for any new frame.\n Note 2 : pledgedSrcSize is only valid once, for the next frame.\n          It's discarded at the end of the frame, and replaced by ZSTD_CONTENTSIZE_UNKNOWN.\n Note 3 : Whenever all input data is provided and consumed in a single round,\n          for example with ZSTD_compress2(),\n          or invoking immediately ZSTD_compressStream2(,,,ZSTD_e_end),\n          this value is automatically overridden by srcSize instead.",
      "source_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "source_line": 587
    },
    "evidence": {
      "discovered_by": "export_analyzer",
      "header_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "forwarded_to": null,
      "demangled_name": null
    },
    "mcp": {
      "version": "1.0",
      "input_schema": {
        "type": "object",
        "properties": {
          "cctx": {
            "type": "string",
            "description": "ZSTD_CCtx*"
          },
          "pledgedSrcSize": {
            "type": "integer",
            "description": "unsigned long long"
          }
        },
        "required": [
          "cctx",
          "pledgedSrcSize"
        ]
      },
      "execution": {
        "method": "dll_import",
        "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
        "function_name": "ZSTD_CCtx_setPledgedSrcSize",
        "calling_convention": "stdcall",
        "charset": "ansi"
      }
    },
    "metadata": {
      "is_signed": false,
      "publisher": null
    },
    "execution": {
      "method": "dll_import",
      "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
      "function_name": "ZSTD_CCtx_setPledgedSrcSize",
      "calling_convention": "stdcall",
      "charset": "ansi"
    },
    "return_type": "size_t",
    "parameters": [
      {
        "name": "cctx",
        "type": "ZSTD_CCtx*",
        "required": true,
        "description": "ZSTD_CCtx*"
      },
      {
        "name": "pledgedSrcSize",
        "type": "unsigned long long",
        "required": true,
        "description": "unsigned long long"
      }
    ]
  },
  {
    "name": "ZSTD_CStreamInSize",
    "tool_id": "dll_ZSTD_CStreamInSize_34",
    "kind": "export",
    "ordinal": 34,
    "rva": "0000A5E0",
    "confidence": "guaranteed",
    "confidence_factors": {
      "has_signature": true,
      "has_documentation": true,
      "has_parameters": true,
      "has_return_type": true,
      "is_forwarded": true,
      "is_ordinal_only": false
    },
    "signature": {
      "return_type": "size_t",
      "parameters": "void",
      "calling_convention": null,
      "full_prototype": "ZSTDLIB_API size_t ZSTD_CStreamInSize(void)"
    },
    "documentation": {
      "summary": "*< recommended size for input buffer",
      "description": "*< recommended size for input buffer",
      "source_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "source_line": 842
    },
    "evidence": {
      "discovered_by": "export_analyzer",
      "header_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "forwarded_to": null,
      "demangled_name": null
    },
    "mcp": {
      "version": "1.0",
      "input_schema": {
        "type": "object",
        "properties": {},
        "required": []
      },
      "execution": {
        "method": "dll_import",
        "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
        "function_name": "ZSTD_CStreamInSize",
        "calling_convention": "stdcall",
        "charset": "ansi"
      }
    },
    "metadata": {
      "is_signed": false,
      "publisher": null
    },
    "execution": {
      "method": "dll_import",
      "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
      "function_name": "ZSTD_CStreamInSize",
      "calling_convention": "stdcall",
      "charset": "ansi"
    },
    "return_type": "size_t"
  },
  {
    "name": "ZSTD_CStreamOutSize",
    "tool_id": "dll_ZSTD_CStreamOutSize_35",
    "kind": "export",
    "ordinal": 35,
    "rva": "0000A5F0",
    "confidence": "guaranteed",
    "confidence_factors": {
      "has_signature": true,
      "has_documentation": true,
      "has_parameters": true,
      "has_return_type": true,
      "is_forwarded": true,
      "is_ordinal_only": false
    },
    "signature": {
      "return_type": "size_t",
      "parameters": "void",
      "calling_convention": null,
      "full_prototype": "ZSTDLIB_API size_t ZSTD_CStreamOutSize(void)"
    },
    "documentation": {
      "summary": "*< recommended size for output buffer. Guarantee to successfully flush at least one complete compressed block.",
      "description": "*< recommended size for output buffer. Guarantee to successfully flush at least one complete compressed block.",
      "source_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "source_line": 843
    },
    "evidence": {
      "discovered_by": "export_analyzer",
      "header_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "forwarded_to": null,
      "demangled_name": null
    },
    "mcp": {
      "version": "1.0",
      "input_schema": {
        "type": "object",
        "properties": {},
        "required": []
      },
      "execution": {
        "method": "dll_import",
        "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
        "function_name": "ZSTD_CStreamOutSize",
        "calling_convention": "stdcall",
        "charset": "ansi"
      }
    },
    "metadata": {
      "is_signed": false,
      "publisher": null
    },
    "execution": {
      "method": "dll_import",
      "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
      "function_name": "ZSTD_CStreamOutSize",
      "calling_convention": "stdcall",
      "charset": "ansi"
    },
    "return_type": "size_t"
  },
  {
    "name": "ZSTD_DCtx_getParameter",
    "tool_id": "dll_ZSTD_DCtx_getParameter_36",
    "kind": "export",
    "ordinal": 36,
    "rva": "00064AE0",
    "confidence": "guaranteed",
    "confidence_factors": {
      "has_signature": true,
      "has_documentation": true,
      "has_parameters": true,
      "has_return_type": true,
      "is_forwarded": true,
      "is_ordinal_only": false
    },
    "signature": {
      "return_type": "size_t",
      "parameters": "ZSTD_DCtx* dctx, ZSTD_dParameter param, int* value",
      "calling_convention": null,
      "full_prototype": "ZSTDLIB_STATIC_API size_t ZSTD_DCtx_getParameter(ZSTD_DCtx* dctx, ZSTD_dParameter param, int* value)"
    },
    "documentation": {
      "summary": "! ZSTD_DCtx_getParameter() :\n Get the requested decompression parameter value, selected by enum ZSTD_dParameter,\n and store it into int* value.\n@return : 0, or an error code (which can be tested with ZSTD_isError()).",
      "description": "! ZSTD_DCtx_getParameter() :\n Get the requested decompression parameter value, selected by enum ZSTD_dParameter,\n and store it into int* value.\n@return : 0, or an error code (which can be tested with ZSTD_isError()).",
      "source_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "source_line": 2495
    },
    "evidence": {
      "discovered_by": "export_analyzer",
      "header_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "forwarded_to": null,
      "demangled_name": null
    },
    "mcp": {
      "version": "1.0",
      "input_schema": {
        "type": "object",
        "properties": {
          "dctx": {
            "type": "string",
            "description": "ZSTD_DCtx*"
          },
          "param": {
            "type": "string",
            "description": "ZSTD_dParameter"
          },
          "value": {
            "type": "string",
            "description": "int*"
          }
        },
        "required": [
          "dctx",
          "param",
          "value"
        ]
      },
      "execution": {
        "method": "dll_import",
        "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
        "function_name": "ZSTD_DCtx_getParameter",
        "calling_convention": "stdcall",
        "charset": "ansi"
      }
    },
    "metadata": {
      "is_signed": false,
      "publisher": null
    },
    "execution": {
      "method": "dll_import",
      "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
      "function_name": "ZSTD_DCtx_getParameter",
      "calling_convention": "stdcall",
      "charset": "ansi"
    },
    "return_type": "size_t",
    "parameters": [
      {
        "name": "dctx",
        "type": "ZSTD_DCtx*",
        "required": true,
        "description": "ZSTD_DCtx*"
      },
      {
        "name": "param",
        "type": "ZSTD_dParameter",
        "required": true,
        "description": "ZSTD_dParameter"
      },
      {
        "name": "value",
        "type": "int*",
        "required": true,
        "description": "int*"
      }
    ]
  },
  {
    "name": "ZSTD_DCtx_loadDictionary",
    "tool_id": "dll_ZSTD_DCtx_loadDictionary_37",
    "kind": "export",
    "ordinal": 37,
    "rva": "00064B70",
    "confidence": "guaranteed",
    "confidence_factors": {
      "has_signature": true,
      "has_documentation": true,
      "has_parameters": true,
      "has_return_type": true,
      "is_forwarded": true,
      "is_ordinal_only": false
    },
    "signature": {
      "return_type": "size_t",
      "parameters": "ZSTD_DCtx* dctx, const void* dict, size_t dictSize",
      "calling_convention": null,
      "full_prototype": "ZSTDLIB_API size_t ZSTD_DCtx_loadDictionary(ZSTD_DCtx* dctx, const void* dict, size_t dictSize)"
    },
    "documentation": {
      "summary": "! ZSTD_DCtx_loadDictionary() : Requires v1.4.0+\n Create an internal DDict from dict buffer, to be used to decompress all future frames.\n The dictionary remains valid for all future frames, until explicitly invalidated, or\n a new dictionary is loaded.\n@result : 0, or an error code (which can be tested with ZSTD_isError()).\n Special : Adding a NULL (or 0-size) dictionary invalidates any previous dictionary,\n           meaning \"return to no-dictionary mode\".\n Note 1 : Loading a dictionary involves building tables,\n          which has a non-negligible impact on CPU usage and latency.\n          It's recommended to \"load once, use many times\", to amortize the cost\n Note 2 :`dict` content will be copied internally, so `dict` can be released after loading.\n          Use ZSTD_DCtx_loadDictionary_byReference() to reference dictionary content instead.\n Note 3 : Use ZSTD_DCtx_loadDictionary_advanced() to take control of\n          how dictionary content is loaded and interpreted.",
      "description": "! ZSTD_DCtx_loadDictionary() : Requires v1.4.0+\n Create an internal DDict from dict buffer, to be used to decompress all future frames.\n The dictionary remains valid for all future frames, until explicitly invalidated, or\n a new dictionary is loaded.\n@result : 0, or an error code (which can be tested with ZSTD_isError()).\n Special : Adding a NULL (or 0-size) dictionary invalidates any previous dictionary,\n           meaning \"return to no-dictionary mode\".\n Note 1 : Loading a dictionary involves building tables,\n          which has a non-negligible impact on CPU usage and latency.\n          It's recommended to \"load once, use many times\", to amortize the cost\n Note 2 :`dict` content will be copied internally, so `dict` can be released after loading.\n          Use ZSTD_DCtx_loadDictionary_byReference() to reference dictionary content instead.\n Note 3 : Use ZSTD_DCtx_loadDictionary_advanced() to take control of\n          how dictionary content is loaded and interpreted.",
      "source_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "source_line": 1161
    },
    "evidence": {
      "discovered_by": "export_analyzer",
      "header_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "forwarded_to": null,
      "demangled_name": null
    },
    "mcp": {
      "version": "1.0",
      "input_schema": {
        "type": "object",
        "properties": {
          "dctx": {
            "type": "string",
            "description": "ZSTD_DCtx*"
          },
          "dict": {
            "type": "string",
            "description": "void*"
          },
          "dictSize": {
            "type": "integer",
            "description": "size_t"
          }
        },
        "required": [
          "dctx",
          "dict",
          "dictSize"
        ]
      },
      "execution": {
        "method": "dll_import",
        "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
        "function_name": "ZSTD_DCtx_loadDictionary",
        "calling_convention": "stdcall",
        "charset": "ansi"
      }
    },
    "metadata": {
      "is_signed": false,
      "publisher": null
    },
    "execution": {
      "method": "dll_import",
      "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
      "function_name": "ZSTD_DCtx_loadDictionary",
      "calling_convention": "stdcall",
      "charset": "ansi"
    },
    "return_type": "size_t",
    "parameters": [
      {
        "name": "dctx",
        "type": "ZSTD_DCtx*",
        "required": true,
        "description": "ZSTD_DCtx*"
      },
      {
        "name": "dict",
        "type": "const void*",
        "required": true,
        "description": "const void*"
      },
      {
        "name": "dictSize",
        "type": "size_t",
        "required": true,
        "description": "size_t"
      }
    ]
  },
  {
    "name": "ZSTD_DCtx_loadDictionary_advanced",
    "tool_id": "dll_ZSTD_DCtx_loadDictionary_advanced_38",
    "kind": "export",
    "ordinal": 38,
    "rva": "00064B90",
    "confidence": "guaranteed",
    "confidence_factors": {
      "has_signature": true,
      "has_documentation": true,
      "has_parameters": true,
      "has_return_type": true,
      "is_forwarded": true,
      "is_ordinal_only": false
    },
    "signature": {
      "return_type": "size_t",
      "parameters": "ZSTD_DCtx* dctx, const void* dict, size_t dictSize, ZSTD_dictLoadMethod_e dictLoadMethod, ZSTD_dictContentType_e dictContentType",
      "calling_convention": null,
      "full_prototype": "ZSTDLIB_STATIC_API size_t ZSTD_DCtx_loadDictionary_advanced(ZSTD_DCtx* dctx, const void* dict, size_t dictSize, ZSTD_dictLoadMethod_e dictLoadMethod, ZSTD_dictContentType_e dictContentType)"
    },
    "documentation": {
      "summary": "! ZSTD_DCtx_loadDictionary_advanced() :\n Same as ZSTD_DCtx_loadDictionary(),\n but gives direct control over\n how to load the dictionary (by copy ? by reference ?)\n and how to interpret it (automatic ? force raw mode ? full mode only ?).",
      "description": "! ZSTD_DCtx_loadDictionary_advanced() :\n Same as ZSTD_DCtx_loadDictionary(),\n but gives direct control over\n how to load the dictionary (by copy ? by reference ?)\n and how to interpret it (automatic ? force raw mode ? full mode only ?).",
      "source_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "source_line": 2474
    },
    "evidence": {
      "discovered_by": "export_analyzer",
      "header_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "forwarded_to": null,
      "demangled_name": null
    },
    "mcp": {
      "version": "1.0",
      "input_schema": {
        "type": "object",
        "properties": {
          "dctx": {
            "type": "string",
            "description": "ZSTD_DCtx*"
          },
          "dict": {
            "type": "string",
            "description": "void*"
          },
          "dictSize": {
            "type": "integer",
            "description": "size_t"
          },
          "dictLoadMethod": {
            "type": "string",
            "description": "ZSTD_dictLoadMethod_e"
          },
          "dictContentType": {
            "type": "string",
            "description": "ZSTD_dictContentType_e"
          }
        },
        "required": [
          "dctx",
          "dict",
          "dictSize",
          "dictLoadMethod",
          "dictContentType"
        ]
      },
      "execution": {
        "method": "dll_import",
        "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
        "function_name": "ZSTD_DCtx_loadDictionary_advanced",
        "calling_convention": "stdcall",
        "charset": "ansi"
      }
    },
    "metadata": {
      "is_signed": false,
      "publisher": null
    },
    "execution": {
      "method": "dll_import",
      "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
      "function_name": "ZSTD_DCtx_loadDictionary_advanced",
      "calling_convention": "stdcall",
      "charset": "ansi"
    },
    "return_type": "size_t",
    "parameters": [
      {
        "name": "dctx",
        "type": "ZSTD_DCtx*",
        "required": true,
        "description": "ZSTD_DCtx*"
      },
      {
        "name": "dict",
        "type": "const void*",
        "required": true,
        "description": "const void*"
      },
      {
        "name": "dictSize",
        "type": "size_t",
        "required": true,
        "description": "size_t"
      },
      {
        "name": "dictLoadMethod",
        "type": "ZSTD_dictLoadMethod_e",
        "required": true,
        "description": "ZSTD_dictLoadMethod_e"
      },
      {
        "name": "dictContentType",
        "type": "ZSTD_dictContentType_e",
        "required": true,
        "description": "ZSTD_dictContentType_e"
      }
    ]
  },
  {
    "name": "ZSTD_DCtx_loadDictionary_byReference",
    "tool_id": "dll_ZSTD_DCtx_loadDictionary_byReference_39",
    "kind": "export",
    "ordinal": 39,
    "rva": "00064C70",
    "confidence": "guaranteed",
    "confidence_factors": {
      "has_signature": true,
      "has_documentation": true,
      "has_parameters": true,
      "has_return_type": true,
      "is_forwarded": true,
      "is_ordinal_only": false
    },
    "signature": {
      "return_type": "size_t",
      "parameters": "ZSTD_DCtx* dctx, const void* dict, size_t dictSize",
      "calling_convention": null,
      "full_prototype": "ZSTDLIB_STATIC_API size_t ZSTD_DCtx_loadDictionary_byReference(ZSTD_DCtx* dctx, const void* dict, size_t dictSize)"
    },
    "documentation": {
      "summary": "! ZSTD_DCtx_loadDictionary_byReference() :\n Same as ZSTD_DCtx_loadDictionary(),\n but references `dict` content instead of copying it into `dctx`.\n This saves memory if `dict` remains around.,\n However, it's imperative that `dict` remains accessible (and unmodified) while being used, so it must outlive decompression.",
      "description": "! ZSTD_DCtx_loadDictionary_byReference() :\n Same as ZSTD_DCtx_loadDictionary(),\n but references `dict` content instead of copying it into `dctx`.\n This saves memory if `dict` remains around.,\n However, it's imperative that `dict` remains accessible (and unmodified) while being used, so it must outlive decompression.",
      "source_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "source_line": 2467
    },
    "evidence": {
      "discovered_by": "export_analyzer",
      "header_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "forwarded_to": null,
      "demangled_name": null
    },
    "mcp": {
      "version": "1.0",
      "input_schema": {
        "type": "object",
        "properties": {
          "dctx": {
            "type": "string",
            "description": "ZSTD_DCtx*"
          },
          "dict": {
            "type": "string",
            "description": "void*"
          },
          "dictSize": {
            "type": "integer",
            "description": "size_t"
          }
        },
        "required": [
          "dctx",
          "dict",
          "dictSize"
        ]
      },
      "execution": {
        "method": "dll_import",
        "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
        "function_name": "ZSTD_DCtx_loadDictionary_byReference",
        "calling_convention": "stdcall",
        "charset": "ansi"
      }
    },
    "metadata": {
      "is_signed": false,
      "publisher": null
    },
    "execution": {
      "method": "dll_import",
      "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
      "function_name": "ZSTD_DCtx_loadDictionary_byReference",
      "calling_convention": "stdcall",
      "charset": "ansi"
    },
    "return_type": "size_t",
    "parameters": [
      {
        "name": "dctx",
        "type": "ZSTD_DCtx*",
        "required": true,
        "description": "ZSTD_DCtx*"
      },
      {
        "name": "dict",
        "type": "const void*",
        "required": true,
        "description": "const void*"
      },
      {
        "name": "dictSize",
        "type": "size_t",
        "required": true,
        "description": "size_t"
      }
    ]
  },
  {
    "name": "ZSTD_DCtx_refDDict",
    "tool_id": "dll_ZSTD_DCtx_refDDict_40",
    "kind": "export",
    "ordinal": 40,
    "rva": "00064C90",
    "confidence": "guaranteed",
    "confidence_factors": {
      "has_signature": true,
      "has_documentation": true,
      "has_parameters": true,
      "has_return_type": true,
      "is_forwarded": true,
      "is_ordinal_only": false
    },
    "signature": {
      "return_type": "size_t",
      "parameters": "ZSTD_DCtx* dctx, const ZSTD_DDict* ddict",
      "calling_convention": null,
      "full_prototype": "ZSTDLIB_API size_t ZSTD_DCtx_refDDict(ZSTD_DCtx* dctx, const ZSTD_DDict* ddict)"
    },
    "documentation": {
      "summary": "! ZSTD_DCtx_refDDict() : Requires v1.4.0+\n Reference a prepared dictionary, to be used to decompress next frames.\n The dictionary remains active for decompression of future frames using same DCtx.\n\n If called with ZSTD_d_refMultipleDDicts enabled, repeated calls of this function\n will store the DDict references in a table, and the DDict used for decompression\n will be determined at decompression time, as per the dict ID in the frame.\n The memory for the table is allocated on the first call to refDDict, and can be\n freed with ZSTD_freeDCtx().\n\n If called with ZSTD_d_refMultipleDDicts disabled (the default), only one dictionary\n will be managed, and referencing a dictionary effectively \"discards\" any previous one.\n\n@result : 0, or an error code (which can be tested with ZSTD_isError()).\n Special: referencing a NULL DDict means \"return to no-dictionary mode\".\n Note 2 : DDict is just referenced, its lifetime must outlive its usage from DCtx.",
      "description": "! ZSTD_DCtx_refDDict() : Requires v1.4.0+\n Reference a prepared dictionary, to be used to decompress next frames.\n The dictionary remains active for decompression of future frames using same DCtx.\n\n If called with ZSTD_d_refMultipleDDicts enabled, repeated calls of this function\n will store the DDict references in a table, and the DDict used for decompression\n will be determined at decompression time, as per the dict ID in the frame.\n The memory for the table is allocated on the first call to refDDict, and can be\n freed with ZSTD_freeDCtx().\n\n If called with ZSTD_d_refMultipleDDicts disabled (the default), only one dictionary\n will be managed, and referencing a dictionary effectively \"discards\" any previous one.\n\n@result : 0, or an error code (which can be tested with ZSTD_isError()).\n Special: referencing a NULL DDict means \"return to no-dictionary mode\".\n Note 2 : DDict is just referenced, its lifetime must outlive its usage from DCtx.",
      "source_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "source_line": 1180
    },
    "evidence": {
      "discovered_by": "export_analyzer",
      "header_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "forwarded_to": null,
      "demangled_name": null
    },
    "mcp": {
      "version": "1.0",
      "input_schema": {
        "type": "object",
        "properties": {
          "dctx": {
            "type": "string",
            "description": "ZSTD_DCtx*"
          },
          "ddict": {
            "type": "string",
            "description": "ZSTD_DDict*"
          }
        },
        "required": [
          "dctx",
          "ddict"
        ]
      },
      "execution": {
        "method": "dll_import",
        "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
        "function_name": "ZSTD_DCtx_refDDict",
        "calling_convention": "stdcall",
        "charset": "ansi"
      }
    },
    "metadata": {
      "is_signed": false,
      "publisher": null
    },
    "execution": {
      "method": "dll_import",
      "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
      "function_name": "ZSTD_DCtx_refDDict",
      "calling_convention": "stdcall",
      "charset": "ansi"
    },
    "return_type": "size_t",
    "parameters": [
      {
        "name": "dctx",
        "type": "ZSTD_DCtx*",
        "required": true,
        "description": "ZSTD_DCtx*"
      },
      {
        "name": "ddict",
        "type": "const ZSTD_DDict*",
        "required": true,
        "description": "const ZSTD_DDict*"
      }
    ]
  },
  {
    "name": "ZSTD_DCtx_refPrefix",
    "tool_id": "dll_ZSTD_DCtx_refPrefix_41",
    "kind": "export",
    "ordinal": 41,
    "rva": "00064F50",
    "confidence": "guaranteed",
    "confidence_factors": {
      "has_signature": true,
      "has_documentation": true,
      "has_parameters": true,
      "has_return_type": true,
      "is_forwarded": true,
      "is_ordinal_only": false
    },
    "signature": {
      "return_type": "size_t",
      "parameters": "ZSTD_DCtx* dctx, const void* prefix, size_t prefixSize",
      "calling_convention": null,
      "full_prototype": "ZSTDLIB_API size_t ZSTD_DCtx_refPrefix(ZSTD_DCtx* dctx, const void* prefix, size_t prefixSize)"
    },
    "documentation": {
      "summary": "! ZSTD_DCtx_refPrefix() : Requires v1.4.0+\n Reference a prefix (single-usage dictionary) to decompress next frame.\n This is the reverse operation of ZSTD_CCtx_refPrefix(),\n and must use the same prefix as the one used during compression.\n Prefix is **only used once**. Reference is discarded at end of frame.\n End of frame is reached when ZSTD_decompressStream() returns 0.\n@result : 0, or an error code (which can be tested with ZSTD_isError()).\n Note 1 : Adding any prefix (including NULL) invalidates any previously set prefix or dictionary\n Note 2 : Prefix buffer is referenced. It **must** outlive decompression.\n          Prefix buffer must remain unmodified up to the end of frame,\n          reached when ZSTD_decompressStream() returns 0.\n Note 3 : By default, the prefix is treated as raw content (ZSTD_dct_rawContent).\n          Use ZSTD_CCtx_refPrefix_advanced() to alter dictMode (Experimental section)\n Note 4 : Referencing a raw content prefix has almost no cpu nor memory cost.\n          A full dictionary is more costly, as it requires building tables.",
      "description": "! ZSTD_DCtx_refPrefix() : Requires v1.4.0+\n Reference a prefix (single-usage dictionary) to decompress next frame.\n This is the reverse operation of ZSTD_CCtx_refPrefix(),\n and must use the same prefix as the one used during compression.\n Prefix is **only used once**. Reference is discarded at end of frame.\n End of frame is reached when ZSTD_decompressStream() returns 0.\n@result : 0, or an error code (which can be tested with ZSTD_isError()).\n Note 1 : Adding any prefix (including NULL) invalidates any previously set prefix or dictionary\n Note 2 : Prefix buffer is referenced. It **must** outlive decompression.\n          Prefix buffer must remain unmodified up to the end of frame,\n          reached when ZSTD_decompressStream() returns 0.\n Note 3 : By default, the prefix is treated as raw content (ZSTD_dct_rawContent).\n          Use ZSTD_CCtx_refPrefix_advanced() to alter dictMode (Experimental section)\n Note 4 : Referencing a raw content prefix has almost no cpu nor memory cost.\n          A full dictionary is more costly, as it requires building tables.",
      "source_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "source_line": 1198
    },
    "evidence": {
      "discovered_by": "export_analyzer",
      "header_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "forwarded_to": null,
      "demangled_name": null
    },
    "mcp": {
      "version": "1.0",
      "input_schema": {
        "type": "object",
        "properties": {
          "dctx": {
            "type": "string",
            "description": "ZSTD_DCtx*"
          },
          "prefix": {
            "type": "string",
            "description": "void*"
          },
          "prefixSize": {
            "type": "integer",
            "description": "size_t"
          }
        },
        "required": [
          "dctx",
          "prefix",
          "prefixSize"
        ]
      },
      "execution": {
        "method": "dll_import",
        "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
        "function_name": "ZSTD_DCtx_refPrefix",
        "calling_convention": "stdcall",
        "charset": "ansi"
      }
    },
    "metadata": {
      "is_signed": false,
      "publisher": null
    },
    "execution": {
      "method": "dll_import",
      "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
      "function_name": "ZSTD_DCtx_refPrefix",
      "calling_convention": "stdcall",
      "charset": "ansi"
    },
    "return_type": "size_t",
    "parameters": [
      {
        "name": "dctx",
        "type": "ZSTD_DCtx*",
        "required": true,
        "description": "ZSTD_DCtx*"
      },
      {
        "name": "prefix",
        "type": "const void*",
        "required": true,
        "description": "const void*"
      },
      {
        "name": "prefixSize",
        "type": "size_t",
        "required": true,
        "description": "size_t"
      }
    ]
  },
  {
    "name": "ZSTD_DCtx_refPrefix_advanced",
    "tool_id": "dll_ZSTD_DCtx_refPrefix_advanced_42",
    "kind": "export",
    "ordinal": 42,
    "rva": "00064F60",
    "confidence": "guaranteed",
    "confidence_factors": {
      "has_signature": true,
      "has_documentation": true,
      "has_parameters": true,
      "has_return_type": true,
      "is_forwarded": true,
      "is_ordinal_only": false
    },
    "signature": {
      "return_type": "size_t",
      "parameters": "ZSTD_DCtx* dctx, const void* prefix, size_t prefixSize, ZSTD_dictContentType_e dictContentType",
      "calling_convention": null,
      "full_prototype": "ZSTDLIB_STATIC_API size_t ZSTD_DCtx_refPrefix_advanced(ZSTD_DCtx* dctx, const void* prefix, size_t prefixSize, ZSTD_dictContentType_e dictContentType)"
    },
    "documentation": {
      "summary": "! ZSTD_DCtx_refPrefix_advanced() :\n Same as ZSTD_DCtx_refPrefix(), but gives finer control over\n how to interpret prefix content (automatic ? force raw mode (default) ? full mode only ?)",
      "description": "! ZSTD_DCtx_refPrefix_advanced() :\n Same as ZSTD_DCtx_refPrefix(), but gives finer control over\n how to interpret prefix content (automatic ? force raw mode (default) ? full mode only ?)",
      "source_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "source_line": 2479
    },
    "evidence": {
      "discovered_by": "export_analyzer",
      "header_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "forwarded_to": null,
      "demangled_name": null
    },
    "mcp": {
      "version": "1.0",
      "input_schema": {
        "type": "object",
        "properties": {
          "dctx": {
            "type": "string",
            "description": "ZSTD_DCtx*"
          },
          "prefix": {
            "type": "string",
            "description": "void*"
          },
          "prefixSize": {
            "type": "integer",
            "description": "size_t"
          },
          "dictContentType": {
            "type": "string",
            "description": "ZSTD_dictContentType_e"
          }
        },
        "required": [
          "dctx",
          "prefix",
          "prefixSize",
          "dictContentType"
        ]
      },
      "execution": {
        "method": "dll_import",
        "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
        "function_name": "ZSTD_DCtx_refPrefix_advanced",
        "calling_convention": "stdcall",
        "charset": "ansi"
      }
    },
    "metadata": {
      "is_signed": false,
      "publisher": null
    },
    "execution": {
      "method": "dll_import",
      "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
      "function_name": "ZSTD_DCtx_refPrefix_advanced",
      "calling_convention": "stdcall",
      "charset": "ansi"
    },
    "return_type": "size_t",
    "parameters": [
      {
        "name": "dctx",
        "type": "ZSTD_DCtx*",
        "required": true,
        "description": "ZSTD_DCtx*"
      },
      {
        "name": "prefix",
        "type": "const void*",
        "required": true,
        "description": "const void*"
      },
      {
        "name": "prefixSize",
        "type": "size_t",
        "required": true,
        "description": "size_t"
      },
      {
        "name": "dictContentType",
        "type": "ZSTD_dictContentType_e",
        "required": true,
        "description": "ZSTD_dictContentType_e"
      }
    ]
  },
  {
    "name": "ZSTD_DCtx_reset",
    "tool_id": "dll_ZSTD_DCtx_reset_43",
    "kind": "export",
    "ordinal": 43,
    "rva": "00065040",
    "confidence": "guaranteed",
    "confidence_factors": {
      "has_signature": true,
      "has_documentation": true,
      "has_parameters": true,
      "has_return_type": true,
      "is_forwarded": true,
      "is_ordinal_only": false
    },
    "signature": {
      "return_type": "size_t",
      "parameters": "ZSTD_DCtx* dctx, ZSTD_ResetDirective reset",
      "calling_convention": null,
      "full_prototype": "ZSTDLIB_API size_t ZSTD_DCtx_reset(ZSTD_DCtx* dctx, ZSTD_ResetDirective reset)"
    },
    "documentation": {
      "summary": "! ZSTD_DCtx_reset() :\n Return a DCtx to clean state.\n Session and parameters can be reset jointly or separately.\n Parameters can only be reset when no active frame is being decompressed.\n@return : 0, or an error code, which can be tested with ZSTD_isError()",
      "description": "! ZSTD_DCtx_reset() :\n Return a DCtx to clean state.\n Session and parameters can be reset jointly or separately.\n Parameters can only be reset when no active frame is being decompressed.\n@return : 0, or an error code, which can be tested with ZSTD_isError()",
      "source_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "source_line": 694
    },
    "evidence": {
      "discovered_by": "export_analyzer",
      "header_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "forwarded_to": null,
      "demangled_name": null
    },
    "mcp": {
      "version": "1.0",
      "input_schema": {
        "type": "object",
        "properties": {
          "dctx": {
            "type": "string",
            "description": "ZSTD_DCtx*"
          },
          "reset": {
            "type": "string",
            "description": "ZSTD_ResetDirective"
          }
        },
        "required": [
          "dctx",
          "reset"
        ]
      },
      "execution": {
        "method": "dll_import",
        "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
        "function_name": "ZSTD_DCtx_reset",
        "calling_convention": "stdcall",
        "charset": "ansi"
      }
    },
    "metadata": {
      "is_signed": false,
      "publisher": null
    },
    "execution": {
      "method": "dll_import",
      "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
      "function_name": "ZSTD_DCtx_reset",
      "calling_convention": "stdcall",
      "charset": "ansi"
    },
    "return_type": "size_t",
    "parameters": [
      {
        "name": "dctx",
        "type": "ZSTD_DCtx*",
        "required": true,
        "description": "ZSTD_DCtx*"
      },
      {
        "name": "reset",
        "type": "ZSTD_ResetDirective",
        "required": true,
        "description": "ZSTD_ResetDirective"
      }
    ]
  },
  {
    "name": "ZSTD_DCtx_setFormat",
    "tool_id": "dll_ZSTD_DCtx_setFormat_44",
    "kind": "export",
    "ordinal": 44,
    "rva": "000651E0",
    "confidence": "guaranteed",
    "confidence_factors": {
      "has_signature": true,
      "has_documentation": false,
      "has_parameters": true,
      "has_return_type": true,
      "is_forwarded": true,
      "is_ordinal_only": false
    },
    "signature": {
      "return_type": "size_t",
      "parameters": "ZSTD_DCtx* dctx, ZSTD_format_e format",
      "calling_convention": null,
      "full_prototype": "size_t ZSTD_DCtx_setFormat(ZSTD_DCtx* dctx, ZSTD_format_e format)"
    },
    "documentation": {
      "summary": "",
      "description": "",
      "source_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "source_line": 2604
    },
    "evidence": {
      "discovered_by": "export_analyzer",
      "header_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "forwarded_to": null,
      "demangled_name": null
    },
    "mcp": {
      "version": "1.0",
      "input_schema": {
        "type": "object",
        "properties": {
          "dctx": {
            "type": "string",
            "description": "ZSTD_DCtx*"
          },
          "format": {
            "type": "string",
            "description": "ZSTD_format_e"
          }
        },
        "required": [
          "dctx",
          "format"
        ]
      },
      "execution": {
        "method": "dll_import",
        "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
        "function_name": "ZSTD_DCtx_setFormat",
        "calling_convention": "stdcall",
        "charset": "ansi"
      }
    },
    "metadata": {
      "is_signed": false,
      "publisher": null
    },
    "execution": {
      "method": "dll_import",
      "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
      "function_name": "ZSTD_DCtx_setFormat",
      "calling_convention": "stdcall",
      "charset": "ansi"
    },
    "return_type": "size_t",
    "parameters": [
      {
        "name": "dctx",
        "type": "ZSTD_DCtx*",
        "required": true,
        "description": "ZSTD_DCtx*"
      },
      {
        "name": "format",
        "type": "ZSTD_format_e",
        "required": true,
        "description": "ZSTD_format_e"
      }
    ]
  },
  {
    "name": "ZSTD_DCtx_setMaxWindowSize",
    "tool_id": "dll_ZSTD_DCtx_setMaxWindowSize_45",
    "kind": "export",
    "ordinal": 45,
    "rva": "00065240",
    "confidence": "guaranteed",
    "confidence_factors": {
      "has_signature": true,
      "has_documentation": true,
      "has_parameters": true,
      "has_return_type": true,
      "is_forwarded": true,
      "is_ordinal_only": false
    },
    "signature": {
      "return_type": "size_t",
      "parameters": "ZSTD_DCtx* dctx, size_t maxWindowSize",
      "calling_convention": null,
      "full_prototype": "ZSTDLIB_STATIC_API size_t ZSTD_DCtx_setMaxWindowSize(ZSTD_DCtx* dctx, size_t maxWindowSize)"
    },
    "documentation": {
      "summary": "! ZSTD_DCtx_setMaxWindowSize() :\n Refuses allocating internal buffers for frames requiring a window size larger than provided limit.\n This protects a decoder context from reserving too much memory for itself (potential attack scenario).\n This parameter is only useful in streaming mode, since no internal buffer is allocated in single-pass mode.\n By default, a decompression context accepts all window sizes <= (1 << ZSTD_WINDOWLOG_LIMIT_DEFAULT)\n@return : 0, or an error code (which can be tested using ZSTD_isError()).",
      "description": "! ZSTD_DCtx_setMaxWindowSize() :\n Refuses allocating internal buffers for frames requiring a window size larger than provided limit.\n This protects a decoder context from reserving too much memory for itself (potential attack scenario).\n This parameter is only useful in streaming mode, since no internal buffer is allocated in single-pass mode.\n By default, a decompression context accepts all window sizes <= (1 << ZSTD_WINDOWLOG_LIMIT_DEFAULT)\n@return : 0, or an error code (which can be tested using ZSTD_isError()).",
      "source_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "source_line": 2488
    },
    "evidence": {
      "discovered_by": "export_analyzer",
      "header_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "forwarded_to": null,
      "demangled_name": null
    },
    "mcp": {
      "version": "1.0",
      "input_schema": {
        "type": "object",
        "properties": {
          "dctx": {
            "type": "string",
            "description": "ZSTD_DCtx*"
          },
          "maxWindowSize": {
            "type": "integer",
            "description": "size_t"
          }
        },
        "required": [
          "dctx",
          "maxWindowSize"
        ]
      },
      "execution": {
        "method": "dll_import",
        "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
        "function_name": "ZSTD_DCtx_setMaxWindowSize",
        "calling_convention": "stdcall",
        "charset": "unicode"
      }
    },
    "metadata": {
      "is_signed": false,
      "publisher": null
    },
    "execution": {
      "method": "dll_import",
      "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
      "function_name": "ZSTD_DCtx_setMaxWindowSize",
      "calling_convention": "stdcall",
      "charset": "unicode"
    },
    "return_type": "size_t",
    "parameters": [
      {
        "name": "dctx",
        "type": "ZSTD_DCtx*",
        "required": true,
        "description": "ZSTD_DCtx*"
      },
      {
        "name": "maxWindowSize",
        "type": "size_t",
        "required": true,
        "description": "size_t"
      }
    ]
  },
  {
    "name": "ZSTD_DCtx_setParameter",
    "tool_id": "dll_ZSTD_DCtx_setParameter_46",
    "kind": "export",
    "ordinal": 46,
    "rva": "000652B0",
    "confidence": "guaranteed",
    "confidence_factors": {
      "has_signature": true,
      "has_documentation": true,
      "has_parameters": true,
      "has_return_type": true,
      "is_forwarded": true,
      "is_ordinal_only": false
    },
    "signature": {
      "return_type": "size_t",
      "parameters": "ZSTD_DCtx* dctx, ZSTD_dParameter param, int value",
      "calling_convention": null,
      "full_prototype": "ZSTDLIB_API size_t ZSTD_DCtx_setParameter(ZSTD_DCtx* dctx, ZSTD_dParameter param, int value)"
    },
    "documentation": {
      "summary": "! ZSTD_DCtx_setParameter() :\n Set one compression parameter, selected by enum ZSTD_dParameter.\n All parameters have valid bounds. Bounds can be queried using ZSTD_dParam_getBounds().\n Providing a value beyond bound will either clamp it, or trigger an error (depending on parameter).\n Setting a parameter is only possible during frame initialization (before starting decompression).\n@return : 0, or an error code (which can be tested using ZSTD_isError()).",
      "description": "! ZSTD_DCtx_setParameter() :\n Set one compression parameter, selected by enum ZSTD_dParameter.\n All parameters have valid bounds. Bounds can be queried using ZSTD_dParam_getBounds().\n Providing a value beyond bound will either clamp it, or trigger an error (depending on parameter).\n Setting a parameter is only possible during frame initialization (before starting decompression).\n@return : 0, or an error code (which can be tested using ZSTD_isError()).",
      "source_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "source_line": 686
    },
    "evidence": {
      "discovered_by": "export_analyzer",
      "header_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "forwarded_to": null,
      "demangled_name": null
    },
    "mcp": {
      "version": "1.0",
      "input_schema": {
        "type": "object",
        "properties": {
          "dctx": {
            "type": "string",
            "description": "ZSTD_DCtx*"
          },
          "param": {
            "type": "string",
            "description": "ZSTD_dParameter"
          },
          "value": {
            "type": "integer",
            "description": "int"
          }
        },
        "required": [
          "dctx",
          "param",
          "value"
        ]
      },
      "execution": {
        "method": "dll_import",
        "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
        "function_name": "ZSTD_DCtx_setParameter",
        "calling_convention": "stdcall",
        "charset": "ansi"
      }
    },
    "metadata": {
      "is_signed": false,
      "publisher": null
    },
    "execution": {
      "method": "dll_import",
      "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
      "function_name": "ZSTD_DCtx_setParameter",
      "calling_convention": "stdcall",
      "charset": "ansi"
    },
    "return_type": "size_t",
    "parameters": [
      {
        "name": "dctx",
        "type": "ZSTD_DCtx*",
        "required": true,
        "description": "ZSTD_DCtx*"
      },
      {
        "name": "param",
        "type": "ZSTD_dParameter",
        "required": true,
        "description": "ZSTD_dParameter"
      },
      {
        "name": "value",
        "type": "int",
        "required": true,
        "description": "int"
      }
    ]
  },
  {
    "name": "ZSTD_DStreamInSize",
    "tool_id": "dll_ZSTD_DStreamInSize_47",
    "kind": "export",
    "ordinal": 47,
    "rva": "00065580",
    "confidence": "guaranteed",
    "confidence_factors": {
      "has_signature": true,
      "has_documentation": true,
      "has_parameters": true,
      "has_return_type": true,
      "is_forwarded": true,
      "is_ordinal_only": false
    },
    "signature": {
      "return_type": "size_t",
      "parameters": "void",
      "calling_convention": null,
      "full_prototype": "ZSTDLIB_API size_t ZSTD_DStreamInSize(void)"
    },
    "documentation": {
      "summary": "!< recommended size for input buffer",
      "description": "!< recommended size for input buffer",
      "source_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "source_line": 950
    },
    "evidence": {
      "discovered_by": "export_analyzer",
      "header_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "forwarded_to": null,
      "demangled_name": null
    },
    "mcp": {
      "version": "1.0",
      "input_schema": {
        "type": "object",
        "properties": {},
        "required": []
      },
      "execution": {
        "method": "dll_import",
        "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
        "function_name": "ZSTD_DStreamInSize",
        "calling_convention": "stdcall",
        "charset": "ansi"
      }
    },
    "metadata": {
      "is_signed": false,
      "publisher": null
    },
    "execution": {
      "method": "dll_import",
      "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
      "function_name": "ZSTD_DStreamInSize",
      "calling_convention": "stdcall",
      "charset": "ansi"
    },
    "return_type": "size_t"
  },
  {
    "name": "ZSTD_DStreamOutSize",
    "tool_id": "dll_ZSTD_DStreamOutSize_48",
    "kind": "export",
    "ordinal": 48,
    "rva": "0000A5E0",
    "confidence": "guaranteed",
    "confidence_factors": {
      "has_signature": true,
      "has_documentation": true,
      "has_parameters": true,
      "has_return_type": true,
      "is_forwarded": true,
      "is_ordinal_only": false
    },
    "signature": {
      "return_type": "size_t",
      "parameters": "void",
      "calling_convention": null,
      "full_prototype": "ZSTDLIB_API size_t ZSTD_DStreamOutSize(void)"
    },
    "documentation": {
      "summary": "!< recommended size for output buffer. Guarantee to successfully flush at least one complete block in all circumstances.",
      "description": "!< recommended size for output buffer. Guarantee to successfully flush at least one complete block in all circumstances.",
      "source_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "source_line": 951
    },
    "evidence": {
      "discovered_by": "export_analyzer",
      "header_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "forwarded_to": null,
      "demangled_name": null
    },
    "mcp": {
      "version": "1.0",
      "input_schema": {
        "type": "object",
        "properties": {},
        "required": []
      },
      "execution": {
        "method": "dll_import",
        "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
        "function_name": "ZSTD_DStreamOutSize",
        "calling_convention": "stdcall",
        "charset": "ansi"
      }
    },
    "metadata": {
      "is_signed": false,
      "publisher": null
    },
    "execution": {
      "method": "dll_import",
      "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
      "function_name": "ZSTD_DStreamOutSize",
      "calling_convention": "stdcall",
      "charset": "ansi"
    },
    "return_type": "size_t"
  },
  {
    "name": "ZSTD_adjustCParams",
    "tool_id": "dll_ZSTD_adjustCParams_49",
    "kind": "export",
    "ordinal": 49,
    "rva": "0000A600",
    "confidence": "guaranteed",
    "confidence_factors": {
      "has_signature": true,
      "has_documentation": true,
      "has_parameters": true,
      "has_return_type": true,
      "is_forwarded": true,
      "is_ordinal_only": false
    },
    "signature": {
      "return_type": "ZSTD_compressionParameters",
      "parameters": "ZSTD_compressionParameters cPar, unsigned long long srcSize, size_t dictSize",
      "calling_convention": null,
      "full_prototype": "ZSTDLIB_STATIC_API ZSTD_compressionParameters ZSTD_adjustCParams(ZSTD_compressionParameters cPar, unsigned long long srcSize, size_t dictSize)"
    },
    "documentation": {
      "summary": "! ZSTD_adjustCParams() :\n optimize params for a given `srcSize` and `dictSize`.\n`srcSize` can be unknown, in which case use ZSTD_CONTENTSIZE_UNKNOWN.\n`dictSize` must be `0` when there is no dictionary.\n cPar can be invalid : all parameters will be clamped within valid range in the @return struct.\n This function never fails (wide contract)",
      "description": "! ZSTD_adjustCParams() :\n optimize params for a given `srcSize` and `dictSize`.\n`srcSize` can be unknown, in which case use ZSTD_CONTENTSIZE_UNKNOWN.\n`dictSize` must be `0` when there is no dictionary.\n cPar can be invalid : all parameters will be clamped within valid range in the @return struct.\n This function never fails (wide contract)",
      "source_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "source_line": 1962
    },
    "evidence": {
      "discovered_by": "export_analyzer",
      "header_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "forwarded_to": null,
      "demangled_name": null
    },
    "mcp": {
      "version": "1.0",
      "input_schema": {
        "type": "object",
        "properties": {
          "cPar": {
            "type": "string",
            "description": "ZSTD_compressionParameters"
          },
          "srcSize": {
            "type": "integer",
            "description": "unsigned long long"
          },
          "dictSize": {
            "type": "integer",
            "description": "size_t"
          }
        },
        "required": [
          "cPar",
          "srcSize",
          "dictSize"
        ]
      },
      "execution": {
        "method": "dll_import",
        "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
        "function_name": "ZSTD_adjustCParams",
        "calling_convention": "stdcall",
        "charset": "ansi"
      }
    },
    "metadata": {
      "is_signed": false,
      "publisher": null
    },
    "execution": {
      "method": "dll_import",
      "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
      "function_name": "ZSTD_adjustCParams",
      "calling_convention": "stdcall",
      "charset": "ansi"
    },
    "return_type": "ZSTD_compressionParameters",
    "parameters": [
      {
        "name": "cPar",
        "type": "ZSTD_compressionParameters",
        "required": true,
        "description": "ZSTD_compressionParameters"
      },
      {
        "name": "srcSize",
        "type": "unsigned long long",
        "required": true,
        "description": "unsigned long long"
      },
      {
        "name": "dictSize",
        "type": "size_t",
        "required": true,
        "description": "size_t"
      }
    ]
  },
  {
    "name": "ZSTD_cParam_getBounds",
    "tool_id": "dll_ZSTD_cParam_getBounds_50",
    "kind": "export",
    "ordinal": 50,
    "rva": "0000BC00",
    "confidence": "guaranteed",
    "confidence_factors": {
      "has_signature": true,
      "has_documentation": true,
      "has_parameters": true,
      "has_return_type": true,
      "is_forwarded": true,
      "is_ordinal_only": false
    },
    "signature": {
      "return_type": "ZSTD_bounds",
      "parameters": "ZSTD_cParameter cParam",
      "calling_convention": null,
      "full_prototype": "ZSTDLIB_API ZSTD_bounds ZSTD_cParam_getBounds(ZSTD_cParameter cParam)"
    },
    "documentation": {
      "summary": "! ZSTD_cParam_getBounds() :\n All parameters must belong to an interval with lower and upper bounds,\n otherwise they will either trigger an error or be automatically clamped.\n@return : a structure, ZSTD_bounds, which contains\n        - an error status field, which must be tested using ZSTD_isError()\n        - lower and upper bounds, both inclusive",
      "description": "! ZSTD_cParam_getBounds() :\n All parameters must belong to an interval with lower and upper bounds,\n otherwise they will either trigger an error or be automatically clamped.\n@return : a structure, ZSTD_bounds, which contains\n        - an error status field, which must be tested using ZSTD_isError()\n        - lower and upper bounds, both inclusive",
      "source_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "source_line": 557
    },
    "evidence": {
      "discovered_by": "export_analyzer",
      "header_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "forwarded_to": null,
      "demangled_name": null
    },
    "mcp": {
      "version": "1.0",
      "input_schema": {
        "type": "object",
        "properties": {
          "cParam": {
            "type": "string",
            "description": "ZSTD_cParameter"
          }
        },
        "required": [
          "cParam"
        ]
      },
      "execution": {
        "method": "dll_import",
        "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
        "function_name": "ZSTD_cParam_getBounds",
        "calling_convention": "stdcall",
        "charset": "ansi"
      }
    },
    "metadata": {
      "is_signed": false,
      "publisher": null
    },
    "execution": {
      "method": "dll_import",
      "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
      "function_name": "ZSTD_cParam_getBounds",
      "calling_convention": "stdcall",
      "charset": "ansi"
    },
    "return_type": "ZSTD_bounds",
    "parameters": [
      {
        "name": "cParam",
        "type": "ZSTD_cParameter",
        "required": true,
        "description": "ZSTD_cParameter"
      }
    ]
  },
  {
    "name": "ZSTD_checkCParams",
    "tool_id": "dll_ZSTD_checkCParams_51",
    "kind": "export",
    "ordinal": 51,
    "rva": "0000BF20",
    "confidence": "guaranteed",
    "confidence_factors": {
      "has_signature": true,
      "has_documentation": true,
      "has_parameters": true,
      "has_return_type": true,
      "is_forwarded": true,
      "is_ordinal_only": false
    },
    "signature": {
      "return_type": "size_t",
      "parameters": "ZSTD_compressionParameters params",
      "calling_convention": null,
      "full_prototype": "ZSTDLIB_STATIC_API size_t ZSTD_checkCParams(ZSTD_compressionParameters params)"
    },
    "documentation": {
      "summary": "! ZSTD_checkCParams() :\n Ensure param values remain within authorized range.\n@return 0 on success, or an error code (can be checked with ZSTD_isError())",
      "description": "! ZSTD_checkCParams() :\n Ensure param values remain within authorized range.\n@return 0 on success, or an error code (can be checked with ZSTD_isError())",
      "source_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "source_line": 1954
    },
    "evidence": {
      "discovered_by": "export_analyzer",
      "header_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "forwarded_to": null,
      "demangled_name": null
    },
    "mcp": {
      "version": "1.0",
      "input_schema": {
        "type": "object",
        "properties": {
          "params": {
            "type": "string",
            "description": "ZSTD_compressionParameters"
          }
        },
        "required": [
          "params"
        ]
      },
      "execution": {
        "method": "dll_import",
        "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
        "function_name": "ZSTD_checkCParams",
        "calling_convention": "stdcall",
        "charset": "ansi"
      }
    },
    "metadata": {
      "is_signed": false,
      "publisher": null
    },
    "execution": {
      "method": "dll_import",
      "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
      "function_name": "ZSTD_checkCParams",
      "calling_convention": "stdcall",
      "charset": "ansi"
    },
    "return_type": "size_t",
    "parameters": [
      {
        "name": "params",
        "type": "ZSTD_compressionParameters",
        "required": true,
        "description": "ZSTD_compressionParameters"
      }
    ]
  },
  {
    "name": "ZSTD_compress",
    "tool_id": "dll_ZSTD_compress_52",
    "kind": "export",
    "ordinal": 52,
    "rva": "0000C010",
    "confidence": "guaranteed",
    "confidence_factors": {
      "has_signature": true,
      "has_documentation": true,
      "has_parameters": true,
      "has_return_type": true,
      "is_forwarded": true,
      "is_ordinal_only": false
    },
    "signature": {
      "return_type": "size_t",
      "parameters": "void* dst, size_t dstCapacity, const void* src, size_t srcSize, int compressionLevel",
      "calling_convention": null,
      "full_prototype": "ZSTDLIB_API size_t ZSTD_compress( void* dst, size_t dstCapacity, const void* src, size_t srcSize, int compressionLevel)"
    },
    "documentation": {
      "summary": "! ZSTD_compress() :\n Compresses `src` content as a single zstd compressed frame into already allocated `dst`.\n NOTE: Providing `dstCapacity >= ZSTD_compressBound(srcSize)` guarantees that zstd will have\n       enough space to successfully compress the data.\n @return : compressed size written into `dst` (<= `dstCapacity),\n           or an error code if it fails (which can be tested using ZSTD_isError()).",
      "description": "! ZSTD_compress() :\n Compresses `src` content as a single zstd compressed frame into already allocated `dst`.\n NOTE: Providing `dstCapacity >= ZSTD_compressBound(srcSize)` guarantees that zstd will have\n       enough space to successfully compress the data.\n @return : compressed size written into `dst` (<= `dstCapacity),\n           or an error code if it fails (which can be tested using ZSTD_isError()).",
      "source_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "source_line": 160
    },
    "evidence": {
      "discovered_by": "export_analyzer",
      "header_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "forwarded_to": null,
      "demangled_name": null
    },
    "mcp": {
      "version": "1.0",
      "input_schema": {
        "type": "object",
        "properties": {
          "dst": {
            "type": "string",
            "description": "void*"
          },
          "dstCapacity": {
            "type": "integer",
            "description": "size_t"
          },
          "src": {
            "type": "string",
            "description": "void*"
          },
          "srcSize": {
            "type": "integer",
            "description": "size_t"
          },
          "compressionLevel": {
            "type": "integer",
            "description": "int"
          }
        },
        "required": [
          "dst",
          "dstCapacity",
          "src",
          "srcSize",
          "compressionLevel"
        ]
      },
      "execution": {
        "method": "dll_import",
        "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
        "function_name": "ZSTD_compress",
        "calling_convention": "stdcall",
        "charset": "ansi"
      }
    },
    "metadata": {
      "is_signed": false,
      "publisher": null
    },
    "execution": {
      "method": "dll_import",
      "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
      "function_name": "ZSTD_compress",
      "calling_convention": "stdcall",
      "charset": "ansi"
    },
    "return_type": "size_t",
    "parameters": [
      {
        "name": "dst",
        "type": "void*",
        "required": true,
        "description": "void*"
      },
      {
        "name": "dstCapacity",
        "type": "size_t",
        "required": true,
        "description": "size_t"
      },
      {
        "name": "src",
        "type": "const void*",
        "required": true,
        "description": "const void*"
      },
      {
        "name": "srcSize",
        "type": "size_t",
        "required": true,
        "description": "size_t"
      },
      {
        "name": "compressionLevel",
        "type": "int",
        "required": true,
        "description": "int"
      }
    ]
  },
  {
    "name": "ZSTD_compress2",
    "tool_id": "dll_ZSTD_compress2_53",
    "kind": "export",
    "ordinal": 53,
    "rva": "0000C330",
    "confidence": "guaranteed",
    "confidence_factors": {
      "has_signature": true,
      "has_documentation": true,
      "has_parameters": true,
      "has_return_type": true,
      "is_forwarded": true,
      "is_ordinal_only": false
    },
    "signature": {
      "return_type": "size_t",
      "parameters": "ZSTD_CCtx* cctx, void* dst, size_t dstCapacity, const void* src, size_t srcSize",
      "calling_convention": null,
      "full_prototype": "ZSTDLIB_API size_t ZSTD_compress2( ZSTD_CCtx* cctx, void* dst, size_t dstCapacity, const void* src, size_t srcSize)"
    },
    "documentation": {
      "summary": "! ZSTD_compress2() :\n Behave the same as ZSTD_compressCCtx(), but compression parameters are set using the advanced API.\n (note that this entry point doesn't even expose a compression level parameter).\n ZSTD_compress2() always starts a new frame.\n Should cctx hold data from a previously unfinished frame, everything about it is forgotten.\n - Compression parameters are pushed into CCtx before starting compression, using ZSTD_CCtx_set*()\n - The function is always blocking, returns when compression is completed.\n NOTE: Providing `dstCapacity >= ZSTD_compressBound(srcSize)` guarantees that zstd will have\n       enough space to successfully compress the data, though it is possible it fails for other reasons.\n@return : compressed size written into `dst` (<= `dstCapacity),\n          or an error code if it fails (which can be tested using ZSTD_isError()).",
      "description": "! ZSTD_compress2() :\n Behave the same as ZSTD_compressCCtx(), but compression parameters are set using the advanced API.\n (note that this entry point doesn't even expose a compression level parameter).\n ZSTD_compress2() always starts a new frame.\n Should cctx hold data from a previously unfinished frame, everything about it is forgotten.\n - Compression parameters are pushed into CCtx before starting compression, using ZSTD_CCtx_set*()\n - The function is always blocking, returns when compression is completed.\n NOTE: Providing `dstCapacity >= ZSTD_compressBound(srcSize)` guarantees that zstd will have\n       enough space to successfully compress the data, though it is possible it fails for other reasons.\n@return : compressed size written into `dst` (<= `dstCapacity),\n          or an error code if it fails (which can be tested using ZSTD_isError()).",
      "source_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "source_line": 623
    },
    "evidence": {
      "discovered_by": "export_analyzer",
      "header_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "forwarded_to": null,
      "demangled_name": null
    },
    "mcp": {
      "version": "1.0",
      "input_schema": {
        "type": "object",
        "properties": {
          "cctx": {
            "type": "string",
            "description": "ZSTD_CCtx*"
          },
          "dst": {
            "type": "string",
            "description": "void*"
          },
          "dstCapacity": {
            "type": "integer",
            "description": "size_t"
          },
          "src": {
            "type": "string",
            "description": "void*"
          },
          "srcSize": {
            "type": "integer",
            "description": "size_t"
          }
        },
        "required": [
          "cctx",
          "dst",
          "dstCapacity",
          "src",
          "srcSize"
        ]
      },
      "execution": {
        "method": "dll_import",
        "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
        "function_name": "ZSTD_compress2",
        "calling_convention": "stdcall",
        "charset": "ansi"
      }
    },
    "metadata": {
      "is_signed": false,
      "publisher": null
    },
    "execution": {
      "method": "dll_import",
      "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
      "function_name": "ZSTD_compress2",
      "calling_convention": "stdcall",
      "charset": "ansi"
    },
    "return_type": "size_t",
    "parameters": [
      {
        "name": "cctx",
        "type": "ZSTD_CCtx*",
        "required": true,
        "description": "ZSTD_CCtx*"
      },
      {
        "name": "dst",
        "type": "void*",
        "required": true,
        "description": "void*"
      },
      {
        "name": "dstCapacity",
        "type": "size_t",
        "required": true,
        "description": "size_t"
      },
      {
        "name": "src",
        "type": "const void*",
        "required": true,
        "description": "const void*"
      },
      {
        "name": "srcSize",
        "type": "size_t",
        "required": true,
        "description": "size_t"
      }
    ]
  },
  {
    "name": "ZSTD_compressBegin",
    "tool_id": "dll_ZSTD_compressBegin_54",
    "kind": "export",
    "ordinal": 54,
    "rva": "0000C630",
    "confidence": "guaranteed",
    "confidence_factors": {
      "has_signature": true,
      "has_documentation": false,
      "has_parameters": true,
      "has_return_type": true,
      "is_forwarded": true,
      "is_ordinal_only": false
    },
    "signature": {
      "return_type": "size_t",
      "parameters": "ZSTD_CCtx* cctx, int compressionLevel",
      "calling_convention": null,
      "full_prototype": "ZSTDLIB_STATIC_API size_t ZSTD_compressBegin(ZSTD_CCtx* cctx, int compressionLevel)"
    },
    "documentation": {
      "summary": "",
      "description": "",
      "source_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "source_line": 3027
    },
    "evidence": {
      "discovered_by": "export_analyzer",
      "header_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "forwarded_to": null,
      "demangled_name": null
    },
    "mcp": {
      "version": "1.0",
      "input_schema": {
        "type": "object",
        "properties": {
          "cctx": {
            "type": "string",
            "description": "ZSTD_CCtx*"
          },
          "compressionLevel": {
            "type": "integer",
            "description": "int"
          }
        },
        "required": [
          "cctx",
          "compressionLevel"
        ]
      },
      "execution": {
        "method": "dll_import",
        "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
        "function_name": "ZSTD_compressBegin",
        "calling_convention": "stdcall",
        "charset": "ansi"
      }
    },
    "metadata": {
      "is_signed": false,
      "publisher": null
    },
    "execution": {
      "method": "dll_import",
      "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
      "function_name": "ZSTD_compressBegin",
      "calling_convention": "stdcall",
      "charset": "ansi"
    },
    "return_type": "size_t",
    "parameters": [
      {
        "name": "cctx",
        "type": "ZSTD_CCtx*",
        "required": true,
        "description": "ZSTD_CCtx*"
      },
      {
        "name": "compressionLevel",
        "type": "int",
        "required": true,
        "description": "int"
      }
    ]
  },
  {
    "name": "ZSTD_compressBegin_advanced",
    "tool_id": "dll_ZSTD_compressBegin_advanced_55",
    "kind": "export",
    "ordinal": 55,
    "rva": "0000C640",
    "confidence": "guaranteed",
    "confidence_factors": {
      "has_signature": true,
      "has_documentation": true,
      "has_parameters": true,
      "has_return_type": true,
      "is_forwarded": true,
      "is_ordinal_only": false
    },
    "signature": {
      "return_type": "size_t",
      "parameters": "ZSTD_CCtx* cctx, const void* dict, size_t dictSize, ZSTD_parameters params, unsigned long long pledgedSrcSize",
      "calling_convention": null,
      "full_prototype": "size_t ZSTD_compressBegin_advanced(ZSTD_CCtx* cctx, const void* dict, size_t dictSize, ZSTD_parameters params, unsigned long long pledgedSrcSize)"
    },
    "documentation": {
      "summary": "*< pledgedSrcSize : If srcSize is not known at init time, use ZSTD_CONTENTSIZE_UNKNOWN",
      "description": "*< pledgedSrcSize : If srcSize is not known at init time, use ZSTD_CONTENTSIZE_UNKNOWN",
      "source_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "source_line": 3045
    },
    "evidence": {
      "discovered_by": "export_analyzer",
      "header_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "forwarded_to": null,
      "demangled_name": null
    },
    "mcp": {
      "version": "1.0",
      "input_schema": {
        "type": "object",
        "properties": {
          "cctx": {
            "type": "string",
            "description": "ZSTD_CCtx*"
          },
          "dict": {
            "type": "string",
            "description": "void*"
          },
          "dictSize": {
            "type": "integer",
            "description": "size_t"
          },
          "params": {
            "type": "string",
            "description": "ZSTD_parameters"
          },
          "pledgedSrcSize": {
            "type": "integer",
            "description": "unsigned long long"
          }
        },
        "required": [
          "cctx",
          "dict",
          "dictSize",
          "params",
          "pledgedSrcSize"
        ]
      },
      "execution": {
        "method": "dll_import",
        "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
        "function_name": "ZSTD_compressBegin_advanced",
        "calling_convention": "stdcall",
        "charset": "ansi"
      }
    },
    "metadata": {
      "is_signed": false,
      "publisher": null
    },
    "execution": {
      "method": "dll_import",
      "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
      "function_name": "ZSTD_compressBegin_advanced",
      "calling_convention": "stdcall",
      "charset": "ansi"
    },
    "return_type": "size_t",
    "parameters": [
      {
        "name": "cctx",
        "type": "ZSTD_CCtx*",
        "required": true,
        "description": "ZSTD_CCtx*"
      },
      {
        "name": "dict",
        "type": "const void*",
        "required": true,
        "description": "const void*"
      },
      {
        "name": "dictSize",
        "type": "size_t",
        "required": true,
        "description": "size_t"
      },
      {
        "name": "params",
        "type": "ZSTD_parameters",
        "required": true,
        "description": "ZSTD_parameters"
      },
      {
        "name": "pledgedSrcSize",
        "type": "unsigned long long",
        "required": true,
        "description": "unsigned long long"
      }
    ]
  },
  {
    "name": "ZSTD_compressBegin_usingCDict",
    "tool_id": "dll_ZSTD_compressBegin_usingCDict_56",
    "kind": "export",
    "ordinal": 56,
    "rva": "0000CB70",
    "confidence": "guaranteed",
    "confidence_factors": {
      "has_signature": true,
      "has_documentation": true,
      "has_parameters": true,
      "has_return_type": true,
      "is_forwarded": true,
      "is_ordinal_only": false
    },
    "signature": {
      "return_type": "size_t",
      "parameters": "ZSTD_CCtx* cctx, const ZSTD_CDict* cdict",
      "calling_convention": null,
      "full_prototype": "ZSTDLIB_STATIC_API size_t ZSTD_compressBegin_usingCDict(ZSTD_CCtx* cctx, const ZSTD_CDict* cdict)"
    },
    "documentation": {
      "summary": "*< note: fails if cdict==NULL",
      "description": "*< note: fails if cdict==NULL",
      "source_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "source_line": 3031
    },
    "evidence": {
      "discovered_by": "export_analyzer",
      "header_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "forwarded_to": null,
      "demangled_name": null
    },
    "mcp": {
      "version": "1.0",
      "input_schema": {
        "type": "object",
        "properties": {
          "cctx": {
            "type": "string",
            "description": "ZSTD_CCtx*"
          },
          "cdict": {
            "type": "string",
            "description": "ZSTD_CDict*"
          }
        },
        "required": [
          "cctx",
          "cdict"
        ]
      },
      "execution": {
        "method": "dll_import",
        "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
        "function_name": "ZSTD_compressBegin_usingCDict",
        "calling_convention": "stdcall",
        "charset": "ansi"
      }
    },
    "metadata": {
      "is_signed": false,
      "publisher": null
    },
    "execution": {
      "method": "dll_import",
      "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
      "function_name": "ZSTD_compressBegin_usingCDict",
      "calling_convention": "stdcall",
      "charset": "ansi"
    },
    "return_type": "size_t",
    "parameters": [
      {
        "name": "cctx",
        "type": "ZSTD_CCtx*",
        "required": true,
        "description": "ZSTD_CCtx*"
      },
      {
        "name": "cdict",
        "type": "const ZSTD_CDict*",
        "required": true,
        "description": "const ZSTD_CDict*"
      }
    ]
  },
  {
    "name": "ZSTD_compressBegin_usingCDict_advanced",
    "tool_id": "dll_ZSTD_compressBegin_usingCDict_advanced_57",
    "kind": "export",
    "ordinal": 57,
    "rva": "0000CBA0",
    "confidence": "guaranteed",
    "confidence_factors": {
      "has_signature": true,
      "has_documentation": true,
      "has_parameters": true,
      "has_return_type": true,
      "is_forwarded": true,
      "is_ordinal_only": false
    },
    "signature": {
      "return_type": "size_t",
      "parameters": "ZSTD_CCtx* const cctx, const ZSTD_CDict* const cdict, ZSTD_frameParameters const fParams, unsigned long long const pledgedSrcSize",
      "calling_convention": null,
      "full_prototype": "size_t ZSTD_compressBegin_usingCDict_advanced(ZSTD_CCtx* const cctx, const ZSTD_CDict* const cdict, ZSTD_frameParameters const fParams, unsigned long long const pledgedSrcSize)"
    },
    "documentation": {
      "summary": "compression parameters are already set within cdict. pledgedSrcSize must be correct. If srcSize is not known, use macro ZSTD_CONTENTSIZE_UNKNOWN",
      "description": "compression parameters are already set within cdict. pledgedSrcSize must be correct. If srcSize is not known, use macro ZSTD_CONTENTSIZE_UNKNOWN",
      "source_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "source_line": 3048
    },
    "evidence": {
      "discovered_by": "export_analyzer",
      "header_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "forwarded_to": null,
      "demangled_name": null
    },
    "mcp": {
      "version": "1.0",
      "input_schema": {
        "type": "object",
        "properties": {
          "cctx": {
            "type": "string",
            "description": "ZSTD_CCtx*"
          },
          "cdict": {
            "type": "string",
            "description": "ZSTD_CDict*"
          },
          "fParams": {
            "type": "string",
            "description": "ZSTD_frameParameters"
          },
          "pledgedSrcSize": {
            "type": "integer",
            "description": "unsigned long long"
          }
        },
        "required": [
          "cctx",
          "cdict",
          "fParams",
          "pledgedSrcSize"
        ]
      },
      "execution": {
        "method": "dll_import",
        "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
        "function_name": "ZSTD_compressBegin_usingCDict_advanced",
        "calling_convention": "stdcall",
        "charset": "ansi"
      }
    },
    "metadata": {
      "is_signed": false,
      "publisher": null
    },
    "execution": {
      "method": "dll_import",
      "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
      "function_name": "ZSTD_compressBegin_usingCDict_advanced",
      "calling_convention": "stdcall",
      "charset": "ansi"
    },
    "return_type": "size_t",
    "parameters": [
      {
        "name": "cctx",
        "type": "ZSTD_CCtx* const",
        "required": true,
        "description": "ZSTD_CCtx* const"
      },
      {
        "name": "cdict",
        "type": "const ZSTD_CDict* const",
        "required": true,
        "description": "const ZSTD_CDict* const"
      },
      {
        "name": "fParams",
        "type": "ZSTD_frameParameters const",
        "required": true,
        "description": "ZSTD_frameParameters const"
      },
      {
        "name": "pledgedSrcSize",
        "type": "unsigned long long const",
        "required": true,
        "description": "unsigned long long const"
      }
    ]
  },
  {
    "name": "ZSTD_compressBegin_usingDict",
    "tool_id": "dll_ZSTD_compressBegin_usingDict_58",
    "kind": "export",
    "ordinal": 58,
    "rva": "0000CF40",
    "confidence": "guaranteed",
    "confidence_factors": {
      "has_signature": true,
      "has_documentation": false,
      "has_parameters": true,
      "has_return_type": true,
      "is_forwarded": true,
      "is_ordinal_only": false
    },
    "signature": {
      "return_type": "size_t",
      "parameters": "ZSTD_CCtx* cctx, const void* dict, size_t dictSize, int compressionLevel",
      "calling_convention": null,
      "full_prototype": "ZSTDLIB_STATIC_API size_t ZSTD_compressBegin_usingDict(ZSTD_CCtx* cctx, const void* dict, size_t dictSize, int compressionLevel)"
    },
    "documentation": {
      "summary": "",
      "description": "",
      "source_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "source_line": 3029
    },
    "evidence": {
      "discovered_by": "export_analyzer",
      "header_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "forwarded_to": null,
      "demangled_name": null
    },
    "mcp": {
      "version": "1.0",
      "input_schema": {
        "type": "object",
        "properties": {
          "cctx": {
            "type": "string",
            "description": "ZSTD_CCtx*"
          },
          "dict": {
            "type": "string",
            "description": "void*"
          },
          "dictSize": {
            "type": "integer",
            "description": "size_t"
          },
          "compressionLevel": {
            "type": "integer",
            "description": "int"
          }
        },
        "required": [
          "cctx",
          "dict",
          "dictSize",
          "compressionLevel"
        ]
      },
      "execution": {
        "method": "dll_import",
        "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
        "function_name": "ZSTD_compressBegin_usingDict",
        "calling_convention": "stdcall",
        "charset": "ansi"
      }
    },
    "metadata": {
      "is_signed": false,
      "publisher": null
    },
    "execution": {
      "method": "dll_import",
      "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
      "function_name": "ZSTD_compressBegin_usingDict",
      "calling_convention": "stdcall",
      "charset": "ansi"
    },
    "return_type": "size_t",
    "parameters": [
      {
        "name": "cctx",
        "type": "ZSTD_CCtx*",
        "required": true,
        "description": "ZSTD_CCtx*"
      },
      {
        "name": "dict",
        "type": "const void*",
        "required": true,
        "description": "const void*"
      },
      {
        "name": "dictSize",
        "type": "size_t",
        "required": true,
        "description": "size_t"
      },
      {
        "name": "compressionLevel",
        "type": "int",
        "required": true,
        "description": "int"
      }
    ]
  },
  {
    "name": "ZSTD_compressBlock",
    "tool_id": "dll_ZSTD_compressBlock_59",
    "kind": "export",
    "ordinal": 59,
    "rva": "0000D260",
    "confidence": "medium",
    "confidence_factors": {
      "has_signature": false,
      "has_documentation": false,
      "has_parameters": false,
      "has_return_type": false,
      "is_forwarded": true,
      "is_ordinal_only": false
    },
    "signature": {
      "return_type": null,
      "parameters": "",
      "calling_convention": null,
      "full_prototype": "void ZSTD_compressBlock()"
    },
    "documentation": {
      "summary": null,
      "description": null,
      "source_file": null,
      "source_line": null
    },
    "evidence": {
      "discovered_by": "export_analyzer",
      "header_file": null,
      "forwarded_to": null,
      "demangled_name": null
    },
    "mcp": {
      "version": "1.0",
      "input_schema": {
        "type": "object",
        "properties": {},
        "required": []
      },
      "execution": {
        "method": "dll_import",
        "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
        "function_name": "ZSTD_compressBlock",
        "calling_convention": "stdcall",
        "charset": "ansi"
      }
    },
    "metadata": {
      "is_signed": false,
      "publisher": null
    },
    "execution": {
      "method": "dll_import",
      "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
      "function_name": "ZSTD_compressBlock",
      "calling_convention": "stdcall",
      "charset": "ansi"
    }
  },
  {
    "name": "ZSTD_compressBound",
    "tool_id": "dll_ZSTD_compressBound_60",
    "kind": "export",
    "ordinal": 60,
    "rva": "0000D920",
    "confidence": "guaranteed",
    "confidence_factors": {
      "has_signature": true,
      "has_documentation": true,
      "has_parameters": true,
      "has_return_type": true,
      "is_forwarded": true,
      "is_ordinal_only": false
    },
    "signature": {
      "return_type": "size_t",
      "parameters": "size_t srcSize",
      "calling_convention": null,
      "full_prototype": "ZSTDLIB_API size_t ZSTD_compressBound(size_t srcSize)"
    },
    "documentation": {
      "summary": "!< maximum compressed size in worst case single-pass scenario",
      "description": "!< maximum compressed size in worst case single-pass scenario",
      "source_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "source_line": 250
    },
    "evidence": {
      "discovered_by": "export_analyzer",
      "header_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "forwarded_to": null,
      "demangled_name": null
    },
    "mcp": {
      "version": "1.0",
      "input_schema": {
        "type": "object",
        "properties": {
          "srcSize": {
            "type": "integer",
            "description": "size_t"
          }
        },
        "required": [
          "srcSize"
        ]
      },
      "execution": {
        "method": "dll_import",
        "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
        "function_name": "ZSTD_compressBound",
        "calling_convention": "stdcall",
        "charset": "ansi"
      }
    },
    "metadata": {
      "is_signed": false,
      "publisher": null
    },
    "execution": {
      "method": "dll_import",
      "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
      "function_name": "ZSTD_compressBound",
      "calling_convention": "stdcall",
      "charset": "ansi"
    },
    "return_type": "size_t",
    "parameters": [
      {
        "name": "srcSize",
        "type": "size_t",
        "required": true,
        "description": "size_t"
      }
    ]
  },
  {
    "name": "ZSTD_compressCCtx",
    "tool_id": "dll_ZSTD_compressCCtx_61",
    "kind": "export",
    "ordinal": 61,
    "rva": "0000D970",
    "confidence": "guaranteed",
    "confidence_factors": {
      "has_signature": true,
      "has_documentation": true,
      "has_parameters": true,
      "has_return_type": true,
      "is_forwarded": true,
      "is_ordinal_only": false
    },
    "signature": {
      "return_type": "size_t",
      "parameters": "ZSTD_CCtx* cctx, void* dst, size_t dstCapacity, const void* src, size_t srcSize, int compressionLevel",
      "calling_convention": null,
      "full_prototype": "ZSTDLIB_API size_t ZSTD_compressCCtx(ZSTD_CCtx* cctx, void* dst, size_t dstCapacity, const void* src, size_t srcSize, int compressionLevel)"
    },
    "documentation": {
      "summary": "! ZSTD_compressCCtx() :\n Same as ZSTD_compress(), using an explicit ZSTD_CCtx.\n Important : in order to mirror `ZSTD_compress()` behavior,\n this function compresses at the requested compression level,\n __ignoring any other advanced parameter__ .\n If any advanced parameter was set using the advanced API,\n they will all be reset. Only @compressionLevel remains.",
      "description": "! ZSTD_compressCCtx() :\n Same as ZSTD_compress(), using an explicit ZSTD_CCtx.\n Important : in order to mirror `ZSTD_compress()` behavior,\n this function compresses at the requested compression level,\n __ignoring any other advanced parameter__ .\n If any advanced parameter was set using the advanced API,\n they will all be reset. Only @compressionLevel remains.",
      "source_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "source_line": 292
    },
    "evidence": {
      "discovered_by": "export_analyzer",
      "header_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "forwarded_to": null,
      "demangled_name": null
    },
    "mcp": {
      "version": "1.0",
      "input_schema": {
        "type": "object",
        "properties": {
          "cctx": {
            "type": "string",
            "description": "ZSTD_CCtx*"
          },
          "dst": {
            "type": "string",
            "description": "void*"
          },
          "dstCapacity": {
            "type": "integer",
            "description": "size_t"
          },
          "src": {
            "type": "string",
            "description": "void*"
          },
          "srcSize": {
            "type": "integer",
            "description": "size_t"
          },
          "compressionLevel": {
            "type": "integer",
            "description": "int"
          }
        },
        "required": [
          "cctx",
          "dst",
          "dstCapacity",
          "src",
          "srcSize",
          "compressionLevel"
        ]
      },
      "execution": {
        "method": "dll_import",
        "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
        "function_name": "ZSTD_compressCCtx",
        "calling_convention": "stdcall",
        "charset": "ansi"
      }
    },
    "metadata": {
      "is_signed": false,
      "publisher": null
    },
    "execution": {
      "method": "dll_import",
      "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
      "function_name": "ZSTD_compressCCtx",
      "calling_convention": "stdcall",
      "charset": "ansi"
    },
    "return_type": "size_t",
    "parameters": [
      {
        "name": "cctx",
        "type": "ZSTD_CCtx*",
        "required": true,
        "description": "ZSTD_CCtx*"
      },
      {
        "name": "dst",
        "type": "void*",
        "required": true,
        "description": "void*"
      },
      {
        "name": "dstCapacity",
        "type": "size_t",
        "required": true,
        "description": "size_t"
      },
      {
        "name": "src",
        "type": "const void*",
        "required": true,
        "description": "const void*"
      },
      {
        "name": "srcSize",
        "type": "size_t",
        "required": true,
        "description": "size_t"
      },
      {
        "name": "compressionLevel",
        "type": "int",
        "required": true,
        "description": "int"
      }
    ]
  },
  {
    "name": "ZSTD_compressContinue",
    "tool_id": "dll_ZSTD_compressContinue_62",
    "kind": "export",
    "ordinal": 62,
    "rva": "0000D9A0",
    "confidence": "guaranteed",
    "confidence_factors": {
      "has_signature": true,
      "has_documentation": false,
      "has_parameters": true,
      "has_return_type": true,
      "is_forwarded": true,
      "is_ordinal_only": false
    },
    "signature": {
      "return_type": "size_t",
      "parameters": "ZSTD_CCtx* cctx, void* dst, size_t dstCapacity, const void* src, size_t srcSize",
      "calling_convention": null,
      "full_prototype": "ZSTDLIB_STATIC_API size_t ZSTD_compressContinue(ZSTD_CCtx* cctx, void* dst, size_t dstCapacity, const void* src, size_t srcSize)"
    },
    "documentation": {
      "summary": "",
      "description": "",
      "source_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "source_line": 3038
    },
    "evidence": {
      "discovered_by": "export_analyzer",
      "header_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "forwarded_to": null,
      "demangled_name": null
    },
    "mcp": {
      "version": "1.0",
      "input_schema": {
        "type": "object",
        "properties": {
          "cctx": {
            "type": "string",
            "description": "ZSTD_CCtx*"
          },
          "dst": {
            "type": "string",
            "description": "void*"
          },
          "dstCapacity": {
            "type": "integer",
            "description": "size_t"
          },
          "src": {
            "type": "string",
            "description": "void*"
          },
          "srcSize": {
            "type": "integer",
            "description": "size_t"
          }
        },
        "required": [
          "cctx",
          "dst",
          "dstCapacity",
          "src",
          "srcSize"
        ]
      },
      "execution": {
        "method": "dll_import",
        "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
        "function_name": "ZSTD_compressContinue",
        "calling_convention": "stdcall",
        "charset": "ansi"
      }
    },
    "metadata": {
      "is_signed": false,
      "publisher": null
    },
    "execution": {
      "method": "dll_import",
      "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
      "function_name": "ZSTD_compressContinue",
      "calling_convention": "stdcall",
      "charset": "ansi"
    },
    "return_type": "size_t",
    "parameters": [
      {
        "name": "cctx",
        "type": "ZSTD_CCtx*",
        "required": true,
        "description": "ZSTD_CCtx*"
      },
      {
        "name": "dst",
        "type": "void*",
        "required": true,
        "description": "void*"
      },
      {
        "name": "dstCapacity",
        "type": "size_t",
        "required": true,
        "description": "size_t"
      },
      {
        "name": "src",
        "type": "const void*",
        "required": true,
        "description": "const void*"
      },
      {
        "name": "srcSize",
        "type": "size_t",
        "required": true,
        "description": "size_t"
      }
    ]
  },
  {
    "name": "ZSTD_compressEnd",
    "tool_id": "dll_ZSTD_compressEnd_63",
    "kind": "export",
    "ordinal": 63,
    "rva": "0000DB90",
    "confidence": "guaranteed",
    "confidence_factors": {
      "has_signature": true,
      "has_documentation": false,
      "has_parameters": true,
      "has_return_type": true,
      "is_forwarded": true,
      "is_ordinal_only": false
    },
    "signature": {
      "return_type": "size_t",
      "parameters": "ZSTD_CCtx* cctx, void* dst, size_t dstCapacity, const void* src, size_t srcSize",
      "calling_convention": null,
      "full_prototype": "ZSTDLIB_STATIC_API size_t ZSTD_compressEnd(ZSTD_CCtx* cctx, void* dst, size_t dstCapacity, const void* src, size_t srcSize)"
    },
    "documentation": {
      "summary": "",
      "description": "",
      "source_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "source_line": 3040
    },
    "evidence": {
      "discovered_by": "export_analyzer",
      "header_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "forwarded_to": null,
      "demangled_name": null
    },
    "mcp": {
      "version": "1.0",
      "input_schema": {
        "type": "object",
        "properties": {
          "cctx": {
            "type": "string",
            "description": "ZSTD_CCtx*"
          },
          "dst": {
            "type": "string",
            "description": "void*"
          },
          "dstCapacity": {
            "type": "integer",
            "description": "size_t"
          },
          "src": {
            "type": "string",
            "description": "void*"
          },
          "srcSize": {
            "type": "integer",
            "description": "size_t"
          }
        },
        "required": [
          "cctx",
          "dst",
          "dstCapacity",
          "src",
          "srcSize"
        ]
      },
      "execution": {
        "method": "dll_import",
        "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
        "function_name": "ZSTD_compressEnd",
        "calling_convention": "stdcall",
        "charset": "ansi"
      }
    },
    "metadata": {
      "is_signed": false,
      "publisher": null
    },
    "execution": {
      "method": "dll_import",
      "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
      "function_name": "ZSTD_compressEnd",
      "calling_convention": "stdcall",
      "charset": "ansi"
    },
    "return_type": "size_t",
    "parameters": [
      {
        "name": "cctx",
        "type": "ZSTD_CCtx*",
        "required": true,
        "description": "ZSTD_CCtx*"
      },
      {
        "name": "dst",
        "type": "void*",
        "required": true,
        "description": "void*"
      },
      {
        "name": "dstCapacity",
        "type": "size_t",
        "required": true,
        "description": "size_t"
      },
      {
        "name": "src",
        "type": "const void*",
        "required": true,
        "description": "const void*"
      },
      {
        "name": "srcSize",
        "type": "size_t",
        "required": true,
        "description": "size_t"
      }
    ]
  },
  {
    "name": "ZSTD_compressSequences",
    "tool_id": "dll_ZSTD_compressSequences_64",
    "kind": "export",
    "ordinal": 64,
    "rva": "0000DFA0",
    "confidence": "guaranteed",
    "confidence_factors": {
      "has_signature": true,
      "has_documentation": true,
      "has_parameters": true,
      "has_return_type": true,
      "is_forwarded": true,
      "is_ordinal_only": false
    },
    "signature": {
      "return_type": "size_t",
      "parameters": "ZSTD_CCtx* cctx, void* dst, size_t dstCapacity, const ZSTD_Sequence* inSeqs, size_t inSeqsSize, const void* src, size_t srcSize",
      "calling_convention": null,
      "full_prototype": "ZSTDLIB_STATIC_API size_t ZSTD_compressSequences(ZSTD_CCtx* cctx, void* dst, size_t dstCapacity, const ZSTD_Sequence* inSeqs, size_t inSeqsSize, const void* src, size_t srcSize)"
    },
    "documentation": {
      "summary": "! ZSTD_compressSequences() :\nCompress an array of ZSTD_Sequence, associated with @src buffer, into dst.\n@src contains the entire input (not just the literals).\nIf @srcSize > sum(sequence.length), the remaining bytes are considered all literals\nIf a dictionary is included, then the cctx should reference the dict (see: ZSTD_CCtx_refCDict(), ZSTD_CCtx_loadDictionary(), etc.).\nThe entire source is compressed into a single frame.\n\nThe compression behavior changes based on cctx params. In particular:\n   If ZSTD_c_blockDelimiters == ZSTD_sf_noBlockDelimiters, the array of ZSTD_Sequence is expected to contain\n   no block delimiters (defined in ZSTD_Sequence). Block boundaries are roughly determined based on\n   the block size derived from the cctx, and sequences may be split. This is the default setting.\n\n   If ZSTD_c_blockDelimiters == ZSTD_sf_explicitBlockDelimiters, the array of ZSTD_Sequence is expected to contain\n   valid block delimiters (defined in ZSTD_Sequence). Behavior is undefined if no block delimiters are provided.\n\n   When ZSTD_c_blockDelimiters == ZSTD_sf_explicitBlockDelimiters, it's possible to decide generating repcodes\n   using the advanced parameter ZSTD_c_repcodeResolution. Repcodes will improve compression ratio, though the benefit\n   can vary greatly depending on Sequences. On the other hand, repcode resolution is an expensive operation.\n   By default, it's disabled at low (<10) compression levels, and enabled above the threshold (>=10).\n   ZSTD_c_repcodeResolution makes it possible to directly manage this processing in either direction.\n\n   If ZSTD_c_validateSequences == 0, this function blindly accepts the Sequences provided. Invalid Sequences cause undefined\n   behavior. If ZSTD_c_validateSequences == 1, then the function will detect invalid Sequences (see doc/zstd_compression_format.md for\n   specifics regarding offset/matchlength requirements) and then bail out and return an error.\n\n   In addition to the two adjustable experimental params, there are other important cctx params.\n   - ZSTD_c_minMatch MUST be set as less than or equal to the smallest match generated by the match finder. It has a minimum value of ZSTD_MINMATCH_MIN.\n   - ZSTD_c_compressionLevel accordingly adjusts the strength of the entropy coder, as it would in typical compression.\n   - ZSTD_c_windowLog affects offset validation: this function will return an error at higher debug levels if a provided offset\n     is larger than what the spec allows for a given window log and dictionary (if present). See: doc/zstd_compression_format.md\n\nNote: Repcodes are, as of now, always re-calculated within this function, ZSTD_Sequence.rep is effectively unused.\nDev Note: Once ability to ingest repcodes become available, the explicit block delims mode must respect those repcodes exactly,\n        and cannot emit an RLE block that disagrees with the repcode history.\n@return : final compressed size, or a ZSTD error code.",
      "description": "! ZSTD_compressSequences() :\nCompress an array of ZSTD_Sequence, associated with @src buffer, into dst.\n@src contains the entire input (not just the literals).\nIf @srcSize > sum(sequence.length), the remaining bytes are considered all literals\nIf a dictionary is included, then the cctx should reference the dict (see: ZSTD_CCtx_refCDict(), ZSTD_CCtx_loadDictionary(), etc.).\nThe entire source is compressed into a single frame.\n\nThe compression behavior changes based on cctx params. In particular:\n   If ZSTD_c_blockDelimiters == ZSTD_sf_noBlockDelimiters, the array of ZSTD_Sequence is expected to contain\n   no block delimiters (defined in ZSTD_Sequence). Block boundaries are roughly determined based on\n   the block size derived from the cctx, and sequences may be split. This is the default setting.\n\n   If ZSTD_c_blockDelimiters == ZSTD_sf_explicitBlockDelimiters, the array of ZSTD_Sequence is expected to contain\n   valid block delimiters (defined in ZSTD_Sequence). Behavior is undefined if no block delimiters are provided.\n\n   When ZSTD_c_blockDelimiters == ZSTD_sf_explicitBlockDelimiters, it's possible to decide generating repcodes\n   using the advanced parameter ZSTD_c_repcodeResolution. Repcodes will improve compression ratio, though the benefit\n   can vary greatly depending on Sequences. On the other hand, repcode resolution is an expensive operation.\n   By default, it's disabled at low (<10) compression levels, and enabled above the threshold (>=10).\n   ZSTD_c_repcodeResolution makes it possible to directly manage this processing in either direction.\n\n   If ZSTD_c_validateSequences == 0, this function blindly accepts the Sequences provided. Invalid Sequences cause undefined\n   behavior. If ZSTD_c_validateSequences == 1, then the function will detect invalid Sequences (see doc/zstd_compression_format.md for\n   specifics regarding offset/matchlength requirements) and then bail out and return an error.\n\n   In addition to the two adjustable experimental params, there are other important cctx params.\n   - ZSTD_c_minMatch MUST be set as less than or equal to the smallest match generated by the match finder. It has a minimum value of ZSTD_MINMATCH_MIN.\n   - ZSTD_c_compressionLevel accordingly adjusts the strength of the entropy coder, as it would in typical compression.\n   - ZSTD_c_windowLog affects offset validation: this function will return an error at higher debug levels if a provided offset\n     is larger than what the spec allows for a given window log and dictionary (if present). See: doc/zstd_compression_format.md\n\nNote: Repcodes are, as of now, always re-calculated within this function, ZSTD_Sequence.rep is effectively unused.\nDev Note: Once ability to ingest repcodes become available, the explicit block delims mode must respect those repcodes exactly,\n        and cannot emit an RLE block that disagrees with the repcode history.\n@return : final compressed size, or a ZSTD error code.",
      "source_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "source_line": 1679
    },
    "evidence": {
      "discovered_by": "export_analyzer",
      "header_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "forwarded_to": null,
      "demangled_name": null
    },
    "mcp": {
      "version": "1.0",
      "input_schema": {
        "type": "object",
        "properties": {
          "cctx": {
            "type": "string",
            "description": "ZSTD_CCtx*"
          },
          "dst": {
            "type": "string",
            "description": "void*"
          },
          "dstCapacity": {
            "type": "integer",
            "description": "size_t"
          },
          "inSeqs": {
            "type": "string",
            "description": "ZSTD_Sequence*"
          },
          "inSeqsSize": {
            "type": "integer",
            "description": "size_t"
          },
          "src": {
            "type": "string",
            "description": "void*"
          },
          "srcSize": {
            "type": "integer",
            "description": "size_t"
          }
        },
        "required": [
          "cctx",
          "dst",
          "dstCapacity",
          "inSeqs",
          "inSeqsSize",
          "src",
          "srcSize"
        ]
      },
      "execution": {
        "method": "dll_import",
        "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
        "function_name": "ZSTD_compressSequences",
        "calling_convention": "stdcall",
        "charset": "ansi"
      }
    },
    "metadata": {
      "is_signed": false,
      "publisher": null
    },
    "execution": {
      "method": "dll_import",
      "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
      "function_name": "ZSTD_compressSequences",
      "calling_convention": "stdcall",
      "charset": "ansi"
    },
    "return_type": "size_t",
    "parameters": [
      {
        "name": "cctx",
        "type": "ZSTD_CCtx*",
        "required": true,
        "description": "ZSTD_CCtx*"
      },
      {
        "name": "dst",
        "type": "void*",
        "required": true,
        "description": "void*"
      },
      {
        "name": "dstCapacity",
        "type": "size_t",
        "required": true,
        "description": "size_t"
      },
      {
        "name": "inSeqs",
        "type": "const ZSTD_Sequence*",
        "required": true,
        "description": "const ZSTD_Sequence*"
      },
      {
        "name": "inSeqsSize",
        "type": "size_t",
        "required": true,
        "description": "size_t"
      },
      {
        "name": "src",
        "type": "const void*",
        "required": true,
        "description": "const void*"
      },
      {
        "name": "srcSize",
        "type": "size_t",
        "required": true,
        "description": "size_t"
      }
    ]
  },
  {
    "name": "ZSTD_compressSequencesAndLiterals",
    "tool_id": "dll_ZSTD_compressSequencesAndLiterals_65",
    "kind": "export",
    "ordinal": 65,
    "rva": "0000E0C0",
    "confidence": "guaranteed",
    "confidence_factors": {
      "has_signature": true,
      "has_documentation": true,
      "has_parameters": true,
      "has_return_type": true,
      "is_forwarded": true,
      "is_ordinal_only": false
    },
    "signature": {
      "return_type": "size_t",
      "parameters": "ZSTD_CCtx* cctx, void* dst, size_t dstCapacity, const ZSTD_Sequence* inSeqs, size_t nbSequences, const void* literals, size_t litSize, size_t litBufCapacity, size_t decompressedSize",
      "calling_convention": null,
      "full_prototype": "ZSTDLIB_STATIC_API size_t ZSTD_compressSequencesAndLiterals(ZSTD_CCtx* cctx, void* dst, size_t dstCapacity, const ZSTD_Sequence* inSeqs, size_t nbSequences, const void* literals, size_t litSize, size_t litBufCapacity, size_t decompressedSize)"
    },
    "documentation": {
      "summary": "! ZSTD_compressSequencesAndLiterals() :\nThis is a variant of ZSTD_compressSequences() which,\ninstead of receiving (src,srcSize) as input parameter, receives (literals,litSize),\naka all the literals, already extracted and laid out into a single continuous buffer.\nThis can be useful if the process generating the sequences also happens to generate the buffer of literals,\nthus skipping an extraction + caching stage.\nIt's a speed optimization, useful when the right conditions are met,\nbut it also features the following limitations:\n- Only supports explicit delimiter mode\n- Currently does not support Sequences validation (so input Sequences are trusted)\n- Not compatible with frame checksum, which must be disabled\n- If any block is incompressible, will fail and return an error\n- @litSize must be == sum of all @.litLength fields in @inSeqs. Any discrepancy will generate an error.\n- @litBufCapacity is the size of the underlying buffer into which literals are written, starting at address @literals.\n  @litBufCapacity must be at least 8 bytes larger than @litSize.\n- @decompressedSize must be correct, and correspond to the sum of all Sequences. Any discrepancy will generate an error.\n@return : final compressed size, or a ZSTD error code.",
      "description": "! ZSTD_compressSequencesAndLiterals() :\nThis is a variant of ZSTD_compressSequences() which,\ninstead of receiving (src,srcSize) as input parameter, receives (literals,litSize),\naka all the literals, already extracted and laid out into a single continuous buffer.\nThis can be useful if the process generating the sequences also happens to generate the buffer of literals,\nthus skipping an extraction + caching stage.\nIt's a speed optimization, useful when the right conditions are met,\nbut it also features the following limitations:\n- Only supports explicit delimiter mode\n- Currently does not support Sequences validation (so input Sequences are trusted)\n- Not compatible with frame checksum, which must be disabled\n- If any block is incompressible, will fail and return an error\n- @litSize must be == sum of all @.litLength fields in @inSeqs. Any discrepancy will generate an error.\n- @litBufCapacity is the size of the underlying buffer into which literals are written, starting at address @literals.\n  @litBufCapacity must be at least 8 bytes larger than @litSize.\n- @decompressedSize must be correct, and correspond to the sum of all Sequences. Any discrepancy will generate an error.\n@return : final compressed size, or a ZSTD error code.",
      "source_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "source_line": 1704
    },
    "evidence": {
      "discovered_by": "export_analyzer",
      "header_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "forwarded_to": null,
      "demangled_name": null
    },
    "mcp": {
      "version": "1.0",
      "input_schema": {
        "type": "object",
        "properties": {
          "cctx": {
            "type": "string",
            "description": "ZSTD_CCtx*"
          },
          "dst": {
            "type": "string",
            "description": "void*"
          },
          "dstCapacity": {
            "type": "integer",
            "description": "size_t"
          },
          "inSeqs": {
            "type": "string",
            "description": "ZSTD_Sequence*"
          },
          "nbSequences": {
            "type": "integer",
            "description": "size_t"
          },
          "literals": {
            "type": "string",
            "description": "void*"
          },
          "litSize": {
            "type": "integer",
            "description": "size_t"
          },
          "litBufCapacity": {
            "type": "integer",
            "description": "size_t"
          },
          "decompressedSize": {
            "type": "integer",
            "description": "size_t"
          }
        },
        "required": [
          "cctx",
          "dst",
          "dstCapacity",
          "inSeqs",
          "nbSequences",
          "literals",
          "litSize",
          "litBufCapacity",
          "decompressedSize"
        ]
      },
      "execution": {
        "method": "dll_import",
        "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
        "function_name": "ZSTD_compressSequencesAndLiterals",
        "calling_convention": "stdcall",
        "charset": "ansi"
      }
    },
    "metadata": {
      "is_signed": false,
      "publisher": null
    },
    "execution": {
      "method": "dll_import",
      "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
      "function_name": "ZSTD_compressSequencesAndLiterals",
      "calling_convention": "stdcall",
      "charset": "ansi"
    },
    "return_type": "size_t",
    "parameters": [
      {
        "name": "cctx",
        "type": "ZSTD_CCtx*",
        "required": true,
        "description": "ZSTD_CCtx*"
      },
      {
        "name": "dst",
        "type": "void*",
        "required": true,
        "description": "void*"
      },
      {
        "name": "dstCapacity",
        "type": "size_t",
        "required": true,
        "description": "size_t"
      },
      {
        "name": "inSeqs",
        "type": "const ZSTD_Sequence*",
        "required": true,
        "description": "const ZSTD_Sequence*"
      },
      {
        "name": "nbSequences",
        "type": "size_t",
        "required": true,
        "description": "size_t"
      },
      {
        "name": "literals",
        "type": "const void*",
        "required": true,
        "description": "const void*"
      },
      {
        "name": "litSize",
        "type": "size_t",
        "required": true,
        "description": "size_t"
      },
      {
        "name": "litBufCapacity",
        "type": "size_t",
        "required": true,
        "description": "size_t"
      },
      {
        "name": "decompressedSize",
        "type": "size_t",
        "required": true,
        "description": "size_t"
      }
    ]
  },
  {
    "name": "ZSTD_compressStream",
    "tool_id": "dll_ZSTD_compressStream_66",
    "kind": "export",
    "ordinal": 66,
    "rva": "0000E910",
    "confidence": "guaranteed",
    "confidence_factors": {
      "has_signature": true,
      "has_documentation": true,
      "has_parameters": true,
      "has_return_type": true,
      "is_forwarded": true,
      "is_ordinal_only": false
    },
    "signature": {
      "return_type": "size_t",
      "parameters": "ZSTD_CStream* zcs, ZSTD_outBuffer* output, ZSTD_inBuffer* input",
      "calling_convention": null,
      "full_prototype": "ZSTDLIB_API size_t ZSTD_compressStream(ZSTD_CStream* zcs, ZSTD_outBuffer* output, ZSTD_inBuffer* input)"
    },
    "documentation": {
      "summary": "!\nAlternative for ZSTD_compressStream2(zcs, output, input, ZSTD_e_continue).\nNOTE: The return value is different. ZSTD_compressStream() returns a hint for\nthe next read size (if non-zero and not an error). ZSTD_compressStream2()\nreturns the minimum nb of bytes left to flush (if non-zero and not an error).",
      "description": "!\nAlternative for ZSTD_compressStream2(zcs, output, input, ZSTD_e_continue).\nNOTE: The return value is different. ZSTD_compressStream() returns a hint for\nthe next read size (if non-zero and not an error). ZSTD_compressStream2()\nreturns the minimum nb of bytes left to flush (if non-zero and not an error).",
      "source_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "source_line": 869
    },
    "evidence": {
      "discovered_by": "export_analyzer",
      "header_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "forwarded_to": null,
      "demangled_name": null
    },
    "mcp": {
      "version": "1.0",
      "input_schema": {
        "type": "object",
        "properties": {
          "zcs": {
            "type": "string",
            "description": "ZSTD_CStream*"
          },
          "output": {
            "type": "string",
            "description": "ZSTD_outBuffer*"
          },
          "input": {
            "type": "string",
            "description": "ZSTD_inBuffer*"
          }
        },
        "required": [
          "zcs",
          "output",
          "input"
        ]
      },
      "execution": {
        "method": "dll_import",
        "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
        "function_name": "ZSTD_compressStream",
        "calling_convention": "stdcall",
        "charset": "ansi"
      }
    },
    "metadata": {
      "is_signed": false,
      "publisher": null
    },
    "execution": {
      "method": "dll_import",
      "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
      "function_name": "ZSTD_compressStream",
      "calling_convention": "stdcall",
      "charset": "ansi"
    },
    "return_type": "size_t",
    "parameters": [
      {
        "name": "zcs",
        "type": "ZSTD_CStream*",
        "required": true,
        "description": "ZSTD_CStream*"
      },
      {
        "name": "output",
        "type": "ZSTD_outBuffer*",
        "required": true,
        "description": "ZSTD_outBuffer*"
      },
      {
        "name": "input",
        "type": "ZSTD_inBuffer*",
        "required": true,
        "description": "ZSTD_inBuffer*"
      }
    ]
  },
  {
    "name": "ZSTD_compressStream2",
    "tool_id": "dll_ZSTD_compressStream2_67",
    "kind": "export",
    "ordinal": 67,
    "rva": "0000E980",
    "confidence": "guaranteed",
    "confidence_factors": {
      "has_signature": true,
      "has_documentation": true,
      "has_parameters": true,
      "has_return_type": true,
      "is_forwarded": true,
      "is_ordinal_only": false
    },
    "signature": {
      "return_type": "size_t",
      "parameters": "ZSTD_CCtx* cctx, ZSTD_outBuffer* output, ZSTD_inBuffer* input, ZSTD_EndDirective endOp",
      "calling_convention": null,
      "full_prototype": "ZSTDLIB_API size_t ZSTD_compressStream2( ZSTD_CCtx* cctx, ZSTD_outBuffer* output, ZSTD_inBuffer* input, ZSTD_EndDirective endOp)"
    },
    "documentation": {
      "summary": "! ZSTD_compressStream2() : Requires v1.4.0+\n Behaves about the same as ZSTD_compressStream, with additional control on end directive.\n - Compression parameters are pushed into CCtx before starting compression, using ZSTD_CCtx_set*()\n - Compression parameters cannot be changed once compression is started (save a list of exceptions in multi-threading mode)\n - output->pos must be <= dstCapacity, input->pos must be <= srcSize\n - output->pos and input->pos will be updated. They are guaranteed to remain below their respective limit.\n - endOp must be a valid directive\n - When nbWorkers==0 (default), function is blocking : it completes its job before returning to caller.\n - When nbWorkers>=1, function is non-blocking : it copies a portion of input, distributes jobs to internal worker threads, flush to output whatever is available,\n                                                 and then immediately returns, just indicating that there is some data remaining to be flushed.\n                                                 The function nonetheless guarantees forward progress : it will return only after it reads or write at least 1+ byte.\n - Exception : if the first call requests a ZSTD_e_end directive and provides enough dstCapacity, the function delegates to ZSTD_compress2() which is always blocking.\n - @return provides a minimum amount of data remaining to be flushed from internal buffers\n           or an error code, which can be tested using ZSTD_isError().\n           if @return != 0, flush is not fully completed, there is still some data left within internal buffers.\n           This is useful for ZSTD_e_flush, since in this case more flushes are necessary to empty all buffers.\n           For ZSTD_e_end, @return == 0 when internal buffers are fully flushed and frame is completed.\n - after a ZSTD_e_end directive, if internal buffer is not fully flushed (@return != 0),\n           only ZSTD_e_end or ZSTD_e_flush operations are allowed.\n           Before starting a new compression job, or changing compression parameters,\n           it is required to fully flush internal buffers.\n - note: if an operation ends with an error, it may leave @cctx in an undefined state.\n         Therefore, it's UB to invoke ZSTD_compressStream2() of ZSTD_compressStream() on such a state.\n         In order to be re-employed after an error, a state must be reset,\n         which can be done explicitly (ZSTD_CCtx_reset()),\n         or is sometimes implied by methods starting a new compression job (ZSTD_initCStream(), ZSTD_compressCCtx())",
      "description": "! ZSTD_compressStream2() : Requires v1.4.0+\n Behaves about the same as ZSTD_compressStream, with additional control on end directive.\n - Compression parameters are pushed into CCtx before starting compression, using ZSTD_CCtx_set*()\n - Compression parameters cannot be changed once compression is started (save a list of exceptions in multi-threading mode)\n - output->pos must be <= dstCapacity, input->pos must be <= srcSize\n - output->pos and input->pos will be updated. They are guaranteed to remain below their respective limit.\n - endOp must be a valid directive\n - When nbWorkers==0 (default), function is blocking : it completes its job before returning to caller.\n - When nbWorkers>=1, function is non-blocking : it copies a portion of input, distributes jobs to internal worker threads, flush to output whatever is available,\n                                                 and then immediately returns, just indicating that there is some data remaining to be flushed.\n                                                 The function nonetheless guarantees forward progress : it will return only after it reads or write at least 1+ byte.\n - Exception : if the first call requests a ZSTD_e_end directive and provides enough dstCapacity, the function delegates to ZSTD_compress2() which is always blocking.\n - @return provides a minimum amount of data remaining to be flushed from internal buffers\n           or an error code, which can be tested using ZSTD_isError().\n           if @return != 0, flush is not fully completed, there is still some data left within internal buffers.\n           This is useful for ZSTD_e_flush, since in this case more flushes are necessary to empty all buffers.\n           For ZSTD_e_end, @return == 0 when internal buffers are fully flushed and frame is completed.\n - after a ZSTD_e_end directive, if internal buffer is not fully flushed (@return != 0),\n           only ZSTD_e_end or ZSTD_e_flush operations are allowed.\n           Before starting a new compression job, or changing compression parameters,\n           it is required to fully flush internal buffers.\n - note: if an operation ends with an error, it may leave @cctx in an undefined state.\n         Therefore, it's UB to invoke ZSTD_compressStream2() of ZSTD_compressStream() on such a state.\n         In order to be re-employed after an error, a state must be reset,\n         which can be done explicitly (ZSTD_CCtx_reset()),\n         or is sometimes implied by methods starting a new compression job (ZSTD_initCStream(), ZSTD_compressCCtx())",
      "source_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "source_line": 823
    },
    "evidence": {
      "discovered_by": "export_analyzer",
      "header_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "forwarded_to": null,
      "demangled_name": null
    },
    "mcp": {
      "version": "1.0",
      "input_schema": {
        "type": "object",
        "properties": {
          "cctx": {
            "type": "string",
            "description": "ZSTD_CCtx*"
          },
          "output": {
            "type": "string",
            "description": "ZSTD_outBuffer*"
          },
          "input": {
            "type": "string",
            "description": "ZSTD_inBuffer*"
          },
          "endOp": {
            "type": "string",
            "description": "ZSTD_EndDirective"
          }
        },
        "required": [
          "cctx",
          "output",
          "input",
          "endOp"
        ]
      },
      "execution": {
        "method": "dll_import",
        "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
        "function_name": "ZSTD_compressStream2",
        "calling_convention": "stdcall",
        "charset": "ansi"
      }
    },
    "metadata": {
      "is_signed": false,
      "publisher": null
    },
    "execution": {
      "method": "dll_import",
      "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
      "function_name": "ZSTD_compressStream2",
      "calling_convention": "stdcall",
      "charset": "ansi"
    },
    "return_type": "size_t",
    "parameters": [
      {
        "name": "cctx",
        "type": "ZSTD_CCtx*",
        "required": true,
        "description": "ZSTD_CCtx*"
      },
      {
        "name": "output",
        "type": "ZSTD_outBuffer*",
        "required": true,
        "description": "ZSTD_outBuffer*"
      },
      {
        "name": "input",
        "type": "ZSTD_inBuffer*",
        "required": true,
        "description": "ZSTD_inBuffer*"
      },
      {
        "name": "endOp",
        "type": "ZSTD_EndDirective",
        "required": true,
        "description": "ZSTD_EndDirective"
      }
    ]
  },
  {
    "name": "ZSTD_compressStream2_simpleArgs",
    "tool_id": "dll_ZSTD_compressStream2_simpleArgs_68",
    "kind": "export",
    "ordinal": 68,
    "rva": "0000EC70",
    "confidence": "guaranteed",
    "confidence_factors": {
      "has_signature": true,
      "has_documentation": true,
      "has_parameters": true,
      "has_return_type": true,
      "is_forwarded": true,
      "is_ordinal_only": false
    },
    "signature": {
      "return_type": "size_t",
      "parameters": "ZSTD_CCtx* cctx, void* dst, size_t dstCapacity, size_t* dstPos, const void* src, size_t srcSize, size_t* srcPos, ZSTD_EndDirective endOp",
      "calling_convention": null,
      "full_prototype": "ZSTDLIB_STATIC_API size_t ZSTD_compressStream2_simpleArgs ( ZSTD_CCtx* cctx, void* dst, size_t dstCapacity, size_t* dstPos, const void* src, size_t srcSize, size_t* srcPos, ZSTD_EndDirective endOp)"
    },
    "documentation": {
      "summary": "! ZSTD_compressStream2_simpleArgs() :\n Same as ZSTD_compressStream2(),\n but using only integral types as arguments.\n This variant might be helpful for binders from dynamic languages\n which have troubles handling structures containing memory pointers.",
      "description": "! ZSTD_compressStream2_simpleArgs() :\n Same as ZSTD_compressStream2(),\n but using only integral types as arguments.\n This variant might be helpful for binders from dynamic languages\n which have troubles handling structures containing memory pointers.",
      "source_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "source_line": 2437
    },
    "evidence": {
      "discovered_by": "export_analyzer",
      "header_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "forwarded_to": null,
      "demangled_name": null
    },
    "mcp": {
      "version": "1.0",
      "input_schema": {
        "type": "object",
        "properties": {
          "cctx": {
            "type": "string",
            "description": "ZSTD_CCtx*"
          },
          "dst": {
            "type": "string",
            "description": "void*"
          },
          "dstCapacity": {
            "type": "integer",
            "description": "size_t"
          },
          "dstPos": {
            "type": "string",
            "description": "size_t*"
          },
          "src": {
            "type": "string",
            "description": "void*"
          },
          "srcSize": {
            "type": "integer",
            "description": "size_t"
          },
          "srcPos": {
            "type": "string",
            "description": "size_t*"
          },
          "endOp": {
            "type": "string",
            "description": "ZSTD_EndDirective"
          }
        },
        "required": [
          "cctx",
          "dst",
          "dstCapacity",
          "dstPos",
          "src",
          "srcSize",
          "srcPos",
          "endOp"
        ]
      },
      "execution": {
        "method": "dll_import",
        "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
        "function_name": "ZSTD_compressStream2_simpleArgs",
        "calling_convention": "stdcall",
        "charset": "ansi"
      }
    },
    "metadata": {
      "is_signed": false,
      "publisher": null
    },
    "execution": {
      "method": "dll_import",
      "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
      "function_name": "ZSTD_compressStream2_simpleArgs",
      "calling_convention": "stdcall",
      "charset": "ansi"
    },
    "return_type": "size_t",
    "parameters": [
      {
        "name": "cctx",
        "type": "ZSTD_CCtx*",
        "required": true,
        "description": "ZSTD_CCtx*"
      },
      {
        "name": "dst",
        "type": "void*",
        "required": true,
        "description": "void*"
      },
      {
        "name": "dstCapacity",
        "type": "size_t",
        "required": true,
        "description": "size_t"
      },
      {
        "name": "dstPos",
        "type": "size_t*",
        "required": true,
        "description": "size_t*"
      },
      {
        "name": "src",
        "type": "const void*",
        "required": true,
        "description": "const void*"
      },
      {
        "name": "srcSize",
        "type": "size_t",
        "required": true,
        "description": "size_t"
      },
      {
        "name": "srcPos",
        "type": "size_t*",
        "required": true,
        "description": "size_t*"
      },
      {
        "name": "endOp",
        "type": "ZSTD_EndDirective",
        "required": true,
        "description": "ZSTD_EndDirective"
      }
    ]
  },
  {
    "name": "ZSTD_compress_advanced",
    "tool_id": "dll_ZSTD_compress_advanced_69",
    "kind": "export",
    "ordinal": 69,
    "rva": "0000F230",
    "confidence": "guaranteed",
    "confidence_factors": {
      "has_signature": true,
      "has_documentation": false,
      "has_parameters": true,
      "has_return_type": true,
      "is_forwarded": true,
      "is_ordinal_only": false
    },
    "signature": {
      "return_type": "size_t",
      "parameters": "ZSTD_CCtx* cctx, void* dst, size_t dstCapacity, const void* src, size_t srcSize, const void* dict,size_t dictSize, ZSTD_parameters params",
      "calling_convention": null,
      "full_prototype": "size_t ZSTD_compress_advanced(ZSTD_CCtx* cctx, void* dst, size_t dstCapacity, const void* src, size_t srcSize, const void* dict,size_t dictSize, ZSTD_parameters params)"
    },
    "documentation": {
      "summary": "",
      "description": "",
      "source_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "source_line": 1991
    },
    "evidence": {
      "discovered_by": "export_analyzer",
      "header_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "forwarded_to": null,
      "demangled_name": null
    },
    "mcp": {
      "version": "1.0",
      "input_schema": {
        "type": "object",
        "properties": {
          "cctx": {
            "type": "string",
            "description": "ZSTD_CCtx*"
          },
          "dst": {
            "type": "string",
            "description": "void*"
          },
          "dstCapacity": {
            "type": "integer",
            "description": "size_t"
          },
          "src": {
            "type": "string",
            "description": "void*"
          },
          "srcSize": {
            "type": "integer",
            "description": "size_t"
          },
          "dict": {
            "type": "string",
            "description": "void*"
          },
          "dictSize": {
            "type": "integer",
            "description": "size_t"
          },
          "params": {
            "type": "string",
            "description": "ZSTD_parameters"
          }
        },
        "required": [
          "cctx",
          "dst",
          "dstCapacity",
          "src",
          "srcSize",
          "dict",
          "dictSize",
          "params"
        ]
      },
      "execution": {
        "method": "dll_import",
        "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
        "function_name": "ZSTD_compress_advanced",
        "calling_convention": "stdcall",
        "charset": "ansi"
      }
    },
    "metadata": {
      "is_signed": false,
      "publisher": null
    },
    "execution": {
      "method": "dll_import",
      "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
      "function_name": "ZSTD_compress_advanced",
      "calling_convention": "stdcall",
      "charset": "ansi"
    },
    "return_type": "size_t",
    "parameters": [
      {
        "name": "cctx",
        "type": "ZSTD_CCtx*",
        "required": true,
        "description": "ZSTD_CCtx*"
      },
      {
        "name": "dst",
        "type": "void*",
        "required": true,
        "description": "void*"
      },
      {
        "name": "dstCapacity",
        "type": "size_t",
        "required": true,
        "description": "size_t"
      },
      {
        "name": "src",
        "type": "const void*",
        "required": true,
        "description": "const void*"
      },
      {
        "name": "srcSize",
        "type": "size_t",
        "required": true,
        "description": "size_t"
      },
      {
        "name": "dict",
        "type": "const void*",
        "required": true,
        "description": "const void*"
      },
      {
        "name": "dictSize",
        "type": "size_t",
        "required": true,
        "description": "size_t"
      },
      {
        "name": "params",
        "type": "ZSTD_parameters",
        "required": true,
        "description": "ZSTD_parameters"
      }
    ]
  },
  {
    "name": "ZSTD_compress_usingCDict",
    "tool_id": "dll_ZSTD_compress_usingCDict_70",
    "kind": "export",
    "ordinal": 70,
    "rva": "0000FA60",
    "confidence": "guaranteed",
    "confidence_factors": {
      "has_signature": true,
      "has_documentation": true,
      "has_parameters": true,
      "has_return_type": true,
      "is_forwarded": true,
      "is_ordinal_only": false
    },
    "signature": {
      "return_type": "size_t",
      "parameters": "ZSTD_CCtx* cctx, void* dst, size_t dstCapacity, const void* src, size_t srcSize, const ZSTD_CDict* cdict",
      "calling_convention": null,
      "full_prototype": "ZSTDLIB_API size_t ZSTD_compress_usingCDict(ZSTD_CCtx* cctx, void* dst, size_t dstCapacity, const void* src, size_t srcSize, const ZSTD_CDict* cdict)"
    },
    "documentation": {
      "summary": "! ZSTD_compress_usingCDict() :\n Compression using a digested Dictionary.\n Recommended when same dictionary is used multiple times.\n Note : compression level is _decided at dictionary creation time_,\n    and frame parameters are hardcoded (dictID=yes, contentSize=yes, checksum=no)",
      "description": "! ZSTD_compress_usingCDict() :\n Compression using a digested Dictionary.\n Recommended when same dictionary is used multiple times.\n Note : compression level is _decided at dictionary creation time_,\n    and frame parameters are hardcoded (dictID=yes, contentSize=yes, checksum=no)",
      "source_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "source_line": 1012
    },
    "evidence": {
      "discovered_by": "export_analyzer",
      "header_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "forwarded_to": null,
      "demangled_name": null
    },
    "mcp": {
      "version": "1.0",
      "input_schema": {
        "type": "object",
        "properties": {
          "cctx": {
            "type": "string",
            "description": "ZSTD_CCtx*"
          },
          "dst": {
            "type": "string",
            "description": "void*"
          },
          "dstCapacity": {
            "type": "integer",
            "description": "size_t"
          },
          "src": {
            "type": "string",
            "description": "void*"
          },
          "srcSize": {
            "type": "integer",
            "description": "size_t"
          },
          "cdict": {
            "type": "string",
            "description": "ZSTD_CDict*"
          }
        },
        "required": [
          "cctx",
          "dst",
          "dstCapacity",
          "src",
          "srcSize",
          "cdict"
        ]
      },
      "execution": {
        "method": "dll_import",
        "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
        "function_name": "ZSTD_compress_usingCDict",
        "calling_convention": "stdcall",
        "charset": "ansi"
      }
    },
    "metadata": {
      "is_signed": false,
      "publisher": null
    },
    "execution": {
      "method": "dll_import",
      "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
      "function_name": "ZSTD_compress_usingCDict",
      "calling_convention": "stdcall",
      "charset": "ansi"
    },
    "return_type": "size_t",
    "parameters": [
      {
        "name": "cctx",
        "type": "ZSTD_CCtx*",
        "required": true,
        "description": "ZSTD_CCtx*"
      },
      {
        "name": "dst",
        "type": "void*",
        "required": true,
        "description": "void*"
      },
      {
        "name": "dstCapacity",
        "type": "size_t",
        "required": true,
        "description": "size_t"
      },
      {
        "name": "src",
        "type": "const void*",
        "required": true,
        "description": "const void*"
      },
      {
        "name": "srcSize",
        "type": "size_t",
        "required": true,
        "description": "size_t"
      },
      {
        "name": "cdict",
        "type": "const ZSTD_CDict*",
        "required": true,
        "description": "const ZSTD_CDict*"
      }
    ]
  },
  {
    "name": "ZSTD_compress_usingCDict_advanced",
    "tool_id": "dll_ZSTD_compress_usingCDict_advanced_71",
    "kind": "export",
    "ordinal": 71,
    "rva": "0000FAF0",
    "confidence": "guaranteed",
    "confidence_factors": {
      "has_signature": true,
      "has_documentation": false,
      "has_parameters": true,
      "has_return_type": true,
      "is_forwarded": true,
      "is_ordinal_only": false
    },
    "signature": {
      "return_type": "size_t",
      "parameters": "ZSTD_CCtx* cctx, void* dst, size_t dstCapacity, const void* src, size_t srcSize, const ZSTD_CDict* cdict, ZSTD_frameParameters fParams",
      "calling_convention": null,
      "full_prototype": "size_t ZSTD_compress_usingCDict_advanced(ZSTD_CCtx* cctx, void* dst, size_t dstCapacity, const void* src, size_t srcSize, const ZSTD_CDict* cdict, ZSTD_frameParameters fParams)"
    },
    "documentation": {
      "summary": "",
      "description": "",
      "source_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "source_line": 2003
    },
    "evidence": {
      "discovered_by": "export_analyzer",
      "header_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "forwarded_to": null,
      "demangled_name": null
    },
    "mcp": {
      "version": "1.0",
      "input_schema": {
        "type": "object",
        "properties": {
          "cctx": {
            "type": "string",
            "description": "ZSTD_CCtx*"
          },
          "dst": {
            "type": "string",
            "description": "void*"
          },
          "dstCapacity": {
            "type": "integer",
            "description": "size_t"
          },
          "src": {
            "type": "string",
            "description": "void*"
          },
          "srcSize": {
            "type": "integer",
            "description": "size_t"
          },
          "cdict": {
            "type": "string",
            "description": "ZSTD_CDict*"
          },
          "fParams": {
            "type": "string",
            "description": "ZSTD_frameParameters"
          }
        },
        "required": [
          "cctx",
          "dst",
          "dstCapacity",
          "src",
          "srcSize",
          "cdict",
          "fParams"
        ]
      },
      "execution": {
        "method": "dll_import",
        "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
        "function_name": "ZSTD_compress_usingCDict_advanced",
        "calling_convention": "stdcall",
        "charset": "ansi"
      }
    },
    "metadata": {
      "is_signed": false,
      "publisher": null
    },
    "execution": {
      "method": "dll_import",
      "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
      "function_name": "ZSTD_compress_usingCDict_advanced",
      "calling_convention": "stdcall",
      "charset": "ansi"
    },
    "return_type": "size_t",
    "parameters": [
      {
        "name": "cctx",
        "type": "ZSTD_CCtx*",
        "required": true,
        "description": "ZSTD_CCtx*"
      },
      {
        "name": "dst",
        "type": "void*",
        "required": true,
        "description": "void*"
      },
      {
        "name": "dstCapacity",
        "type": "size_t",
        "required": true,
        "description": "size_t"
      },
      {
        "name": "src",
        "type": "const void*",
        "required": true,
        "description": "const void*"
      },
      {
        "name": "srcSize",
        "type": "size_t",
        "required": true,
        "description": "size_t"
      },
      {
        "name": "cdict",
        "type": "const ZSTD_CDict*",
        "required": true,
        "description": "const ZSTD_CDict*"
      },
      {
        "name": "fParams",
        "type": "ZSTD_frameParameters",
        "required": true,
        "description": "ZSTD_frameParameters"
      }
    ]
  },
  {
    "name": "ZSTD_compress_usingDict",
    "tool_id": "dll_ZSTD_compress_usingDict_72",
    "kind": "export",
    "ordinal": 72,
    "rva": "0000FB80",
    "confidence": "guaranteed",
    "confidence_factors": {
      "has_signature": true,
      "has_documentation": true,
      "has_parameters": true,
      "has_return_type": true,
      "is_forwarded": true,
      "is_ordinal_only": false
    },
    "signature": {
      "return_type": "size_t",
      "parameters": "ZSTD_CCtx* ctx, void* dst, size_t dstCapacity, const void* src, size_t srcSize, const void* dict,size_t dictSize, int compressionLevel",
      "calling_convention": null,
      "full_prototype": "ZSTDLIB_API size_t ZSTD_compress_usingDict(ZSTD_CCtx* ctx, void* dst, size_t dstCapacity, const void* src, size_t srcSize, const void* dict,size_t dictSize, int compressionLevel)"
    },
    "documentation": {
      "summary": "! ZSTD_compress_usingDict() :\n Compression at an explicit compression level using a Dictionary.\n A dictionary can be any arbitrary data segment (also called a prefix),\n or a buffer with specified information (see zdict.h).\n Note : This function loads the dictionary, resulting in significant startup delay.\n        It's intended for a dictionary used only once.\n Note 2 : When `dict == NULL || dictSize < 8` no dictionary is used.",
      "description": "! ZSTD_compress_usingDict() :\n Compression at an explicit compression level using a Dictionary.\n A dictionary can be any arbitrary data segment (also called a prefix),\n or a buffer with specified information (see zdict.h).\n Note : This function loads the dictionary, resulting in significant startup delay.\n        It's intended for a dictionary used only once.\n Note 2 : When `dict == NULL || dictSize < 8` no dictionary is used.",
      "source_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "source_line": 964
    },
    "evidence": {
      "discovered_by": "export_analyzer",
      "header_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "forwarded_to": null,
      "demangled_name": null
    },
    "mcp": {
      "version": "1.0",
      "input_schema": {
        "type": "object",
        "properties": {
          "ctx": {
            "type": "string",
            "description": "ZSTD_CCtx*"
          },
          "dst": {
            "type": "string",
            "description": "void*"
          },
          "dstCapacity": {
            "type": "integer",
            "description": "size_t"
          },
          "src": {
            "type": "string",
            "description": "void*"
          },
          "srcSize": {
            "type": "integer",
            "description": "size_t"
          },
          "dict": {
            "type": "string",
            "description": "void*"
          },
          "dictSize": {
            "type": "integer",
            "description": "size_t"
          },
          "compressionLevel": {
            "type": "integer",
            "description": "int"
          }
        },
        "required": [
          "ctx",
          "dst",
          "dstCapacity",
          "src",
          "srcSize",
          "dict",
          "dictSize",
          "compressionLevel"
        ]
      },
      "execution": {
        "method": "dll_import",
        "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
        "function_name": "ZSTD_compress_usingDict",
        "calling_convention": "stdcall",
        "charset": "ansi"
      }
    },
    "metadata": {
      "is_signed": false,
      "publisher": null
    },
    "execution": {
      "method": "dll_import",
      "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
      "function_name": "ZSTD_compress_usingDict",
      "calling_convention": "stdcall",
      "charset": "ansi"
    },
    "return_type": "size_t",
    "parameters": [
      {
        "name": "ctx",
        "type": "ZSTD_CCtx*",
        "required": true,
        "description": "ZSTD_CCtx*"
      },
      {
        "name": "dst",
        "type": "void*",
        "required": true,
        "description": "void*"
      },
      {
        "name": "dstCapacity",
        "type": "size_t",
        "required": true,
        "description": "size_t"
      },
      {
        "name": "src",
        "type": "const void*",
        "required": true,
        "description": "const void*"
      },
      {
        "name": "srcSize",
        "type": "size_t",
        "required": true,
        "description": "size_t"
      },
      {
        "name": "dict",
        "type": "const void*",
        "required": true,
        "description": "const void*"
      },
      {
        "name": "dictSize",
        "type": "size_t",
        "required": true,
        "description": "size_t"
      },
      {
        "name": "compressionLevel",
        "type": "int",
        "required": true,
        "description": "int"
      }
    ]
  },
  {
    "name": "ZSTD_copyCCtx",
    "tool_id": "dll_ZSTD_copyCCtx_73",
    "kind": "export",
    "ordinal": 73,
    "rva": "000101C0",
    "confidence": "guaranteed",
    "confidence_factors": {
      "has_signature": true,
      "has_documentation": true,
      "has_parameters": true,
      "has_return_type": true,
      "is_forwarded": true,
      "is_ordinal_only": false
    },
    "signature": {
      "return_type": "size_t",
      "parameters": "ZSTD_CCtx* cctx, const ZSTD_CCtx* preparedCCtx, unsigned long long pledgedSrcSize",
      "calling_convention": null,
      "full_prototype": "size_t ZSTD_copyCCtx(ZSTD_CCtx* cctx, const ZSTD_CCtx* preparedCCtx, unsigned long long pledgedSrcSize)"
    },
    "documentation": {
      "summary": "*<  note: if pledgedSrcSize is not known, use ZSTD_CONTENTSIZE_UNKNOWN",
      "description": "*<  note: if pledgedSrcSize is not known, use ZSTD_CONTENTSIZE_UNKNOWN",
      "source_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "source_line": 3035
    },
    "evidence": {
      "discovered_by": "export_analyzer",
      "header_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "forwarded_to": null,
      "demangled_name": null
    },
    "mcp": {
      "version": "1.0",
      "input_schema": {
        "type": "object",
        "properties": {
          "cctx": {
            "type": "string",
            "description": "ZSTD_CCtx*"
          },
          "preparedCCtx": {
            "type": "string",
            "description": "ZSTD_CCtx*"
          },
          "pledgedSrcSize": {
            "type": "integer",
            "description": "unsigned long long"
          }
        },
        "required": [
          "cctx",
          "preparedCCtx",
          "pledgedSrcSize"
        ]
      },
      "execution": {
        "method": "dll_import",
        "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
        "function_name": "ZSTD_copyCCtx",
        "calling_convention": "stdcall",
        "charset": "ansi"
      }
    },
    "metadata": {
      "is_signed": false,
      "publisher": null
    },
    "execution": {
      "method": "dll_import",
      "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
      "function_name": "ZSTD_copyCCtx",
      "calling_convention": "stdcall",
      "charset": "ansi"
    },
    "return_type": "size_t",
    "parameters": [
      {
        "name": "cctx",
        "type": "ZSTD_CCtx*",
        "required": true,
        "description": "ZSTD_CCtx*"
      },
      {
        "name": "preparedCCtx",
        "type": "const ZSTD_CCtx*",
        "required": true,
        "description": "const ZSTD_CCtx*"
      },
      {
        "name": "pledgedSrcSize",
        "type": "unsigned long long",
        "required": true,
        "description": "unsigned long long"
      }
    ]
  },
  {
    "name": "ZSTD_copyDCtx",
    "tool_id": "dll_ZSTD_copyDCtx_74",
    "kind": "export",
    "ordinal": 74,
    "rva": "00065590",
    "confidence": "guaranteed",
    "confidence_factors": {
      "has_signature": true,
      "has_documentation": false,
      "has_parameters": true,
      "has_return_type": true,
      "is_forwarded": true,
      "is_ordinal_only": false
    },
    "signature": {
      "return_type": "void",
      "parameters": "ZSTD_DCtx* dctx, const ZSTD_DCtx* preparedDCtx",
      "calling_convention": null,
      "full_prototype": "ZSTDLIB_STATIC_API void ZSTD_copyDCtx(ZSTD_DCtx* dctx, const ZSTD_DCtx* preparedDCtx)"
    },
    "documentation": {
      "summary": "",
      "description": "",
      "source_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "source_line": 3135
    },
    "evidence": {
      "discovered_by": "export_analyzer",
      "header_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "forwarded_to": null,
      "demangled_name": null
    },
    "mcp": {
      "version": "1.0",
      "input_schema": {
        "type": "object",
        "properties": {
          "dctx": {
            "type": "string",
            "description": "ZSTD_DCtx*"
          },
          "preparedDCtx": {
            "type": "string",
            "description": "ZSTD_DCtx*"
          }
        },
        "required": [
          "dctx",
          "preparedDCtx"
        ]
      },
      "execution": {
        "method": "dll_import",
        "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
        "function_name": "ZSTD_copyDCtx",
        "calling_convention": "stdcall",
        "charset": "ansi"
      }
    },
    "metadata": {
      "is_signed": false,
      "publisher": null
    },
    "execution": {
      "method": "dll_import",
      "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
      "function_name": "ZSTD_copyDCtx",
      "calling_convention": "stdcall",
      "charset": "ansi"
    },
    "return_type": "void",
    "parameters": [
      {
        "name": "dctx",
        "type": "ZSTD_DCtx*",
        "required": true,
        "description": "ZSTD_DCtx*"
      },
      {
        "name": "preparedDCtx",
        "type": "const ZSTD_DCtx*",
        "required": true,
        "description": "const ZSTD_DCtx*"
      }
    ]
  },
  {
    "name": "ZSTD_createCCtx",
    "tool_id": "dll_ZSTD_createCCtx_75",
    "kind": "export",
    "ordinal": 75,
    "rva": "00010630",
    "confidence": "guaranteed",
    "confidence_factors": {
      "has_signature": true,
      "has_documentation": false,
      "has_parameters": true,
      "has_return_type": true,
      "is_forwarded": true,
      "is_ordinal_only": false
    },
    "signature": {
      "return_type": "ZSTD_CCtx*",
      "parameters": "void",
      "calling_convention": null,
      "full_prototype": "ZSTDLIB_API ZSTD_CCtx* ZSTD_createCCtx(void)"
    },
    "documentation": {
      "summary": "",
      "description": "",
      "source_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "source_line": 281
    },
    "evidence": {
      "discovered_by": "export_analyzer",
      "header_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "forwarded_to": null,
      "demangled_name": null
    },
    "mcp": {
      "version": "1.0",
      "input_schema": {
        "type": "object",
        "properties": {},
        "required": []
      },
      "execution": {
        "method": "dll_import",
        "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
        "function_name": "ZSTD_createCCtx",
        "calling_convention": "stdcall",
        "charset": "ansi"
      }
    },
    "metadata": {
      "is_signed": false,
      "publisher": null
    },
    "execution": {
      "method": "dll_import",
      "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
      "function_name": "ZSTD_createCCtx",
      "calling_convention": "stdcall",
      "charset": "ansi"
    },
    "return_type": "ZSTD_CCtx*"
  },
  {
    "name": "ZSTD_createCCtxParams",
    "tool_id": "dll_ZSTD_createCCtxParams_76",
    "kind": "export",
    "ordinal": 76,
    "rva": "000106A0",
    "confidence": "guaranteed",
    "confidence_factors": {
      "has_signature": true,
      "has_documentation": true,
      "has_parameters": true,
      "has_return_type": true,
      "is_forwarded": true,
      "is_ordinal_only": false
    },
    "signature": {
      "return_type": "ZSTD_CCtx_params*",
      "parameters": "void",
      "calling_convention": null,
      "full_prototype": "ZSTDLIB_STATIC_API ZSTD_CCtx_params* ZSTD_createCCtxParams(void)"
    },
    "documentation": {
      "summary": "! ZSTD_CCtx_params :\n Quick howto :\n - ZSTD_createCCtxParams() : Create a ZSTD_CCtx_params structure\n - ZSTD_CCtxParams_setParameter() : Push parameters one by one into\n                                    an existing ZSTD_CCtx_params structure.\n                                    This is similar to\n                                    ZSTD_CCtx_setParameter().\n - ZSTD_CCtx_setParametersUsingCCtxParams() : Apply parameters to\n                                   an existing CCtx.\n                                   These parameters will be applied to\n                                   all subsequent frames.\n - ZSTD_compressStream2() : Do compression using the CCtx.\n - ZSTD_freeCCtxParams() : Free the memory, accept NULL pointer.\n\n This can be used with ZSTD_estimateCCtxSize_advanced_usingCCtxParams()\n for static allocation of CCtx for single-threaded compression.",
      "description": "! ZSTD_CCtx_params :\n Quick howto :\n - ZSTD_createCCtxParams() : Create a ZSTD_CCtx_params structure\n - ZSTD_CCtxParams_setParameter() : Push parameters one by one into\n                                    an existing ZSTD_CCtx_params structure.\n                                    This is similar to\n                                    ZSTD_CCtx_setParameter().\n - ZSTD_CCtx_setParametersUsingCCtxParams() : Apply parameters to\n                                   an existing CCtx.\n                                   These parameters will be applied to\n                                   all subsequent frames.\n - ZSTD_compressStream2() : Do compression using the CCtx.\n - ZSTD_freeCCtxParams() : Free the memory, accept NULL pointer.\n\n This can be used with ZSTD_estimateCCtxSize_advanced_usingCCtxParams()\n for static allocation of CCtx for single-threaded compression.",
      "source_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "source_line": 2384
    },
    "evidence": {
      "discovered_by": "export_analyzer",
      "header_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "forwarded_to": null,
      "demangled_name": null
    },
    "mcp": {
      "version": "1.0",
      "input_schema": {
        "type": "object",
        "properties": {},
        "required": []
      },
      "execution": {
        "method": "dll_import",
        "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
        "function_name": "ZSTD_createCCtxParams",
        "calling_convention": "stdcall",
        "charset": "ansi"
      }
    },
    "metadata": {
      "is_signed": false,
      "publisher": null
    },
    "execution": {
      "method": "dll_import",
      "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
      "function_name": "ZSTD_createCCtxParams",
      "calling_convention": "stdcall",
      "charset": "ansi"
    },
    "return_type": "ZSTD_CCtx_params*"
  },
  {
    "name": "ZSTD_createCCtx_advanced",
    "tool_id": "dll_ZSTD_createCCtx_advanced_77",
    "kind": "export",
    "ordinal": 77,
    "rva": "00010720",
    "confidence": "guaranteed",
    "confidence_factors": {
      "has_signature": true,
      "has_documentation": false,
      "has_parameters": true,
      "has_return_type": true,
      "is_forwarded": true,
      "is_ordinal_only": false
    },
    "signature": {
      "return_type": "ZSTD_CCtx*",
      "parameters": "ZSTD_customMem customMem",
      "calling_convention": null,
      "full_prototype": "ZSTDLIB_STATIC_API ZSTD_CCtx* ZSTD_createCCtx_advanced(ZSTD_customMem customMem)"
    },
    "documentation": {
      "summary": "",
      "description": "",
      "source_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "source_line": 1885
    },
    "evidence": {
      "discovered_by": "export_analyzer",
      "header_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "forwarded_to": null,
      "demangled_name": null
    },
    "mcp": {
      "version": "1.0",
      "input_schema": {
        "type": "object",
        "properties": {
          "customMem": {
            "type": "string",
            "description": "ZSTD_customMem"
          }
        },
        "required": [
          "customMem"
        ]
      },
      "execution": {
        "method": "dll_import",
        "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
        "function_name": "ZSTD_createCCtx_advanced",
        "calling_convention": "stdcall",
        "charset": "ansi"
      }
    },
    "metadata": {
      "is_signed": false,
      "publisher": null
    },
    "execution": {
      "method": "dll_import",
      "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
      "function_name": "ZSTD_createCCtx_advanced",
      "calling_convention": "stdcall",
      "charset": "ansi"
    },
    "return_type": "ZSTD_CCtx*",
    "parameters": [
      {
        "name": "customMem",
        "type": "ZSTD_customMem",
        "required": true,
        "description": "ZSTD_customMem"
      }
    ]
  },
  {
    "name": "ZSTD_createCDict",
    "tool_id": "dll_ZSTD_createCDict_78",
    "kind": "export",
    "ordinal": 78,
    "rva": "000107C0",
    "confidence": "guaranteed",
    "confidence_factors": {
      "has_signature": true,
      "has_documentation": true,
      "has_parameters": true,
      "has_return_type": true,
      "is_forwarded": true,
      "is_ordinal_only": false
    },
    "signature": {
      "return_type": "ZSTD_CDict*",
      "parameters": "const void* dictBuffer, size_t dictSize, int compressionLevel",
      "calling_convention": null,
      "full_prototype": "ZSTDLIB_API ZSTD_CDict* ZSTD_createCDict(const void* dictBuffer, size_t dictSize, int compressionLevel)"
    },
    "documentation": {
      "summary": "! ZSTD_createCDict() :\n When compressing multiple messages or blocks using the same dictionary,\n it's recommended to digest the dictionary only once, since it's a costly operation.\n ZSTD_createCDict() will create a state from digesting a dictionary.\n The resulting state can be used for future compression operations with very limited startup cost.\n ZSTD_CDict can be created once and shared by multiple threads concurrently, since its usage is read-only.\n@dictBuffer can be released after ZSTD_CDict creation, because its content is copied within CDict.\n Note 1 : Consider experimental function `ZSTD_createCDict_byReference()` if you prefer to not duplicate @dictBuffer content.\n Note 2 : A ZSTD_CDict can be created from an empty @dictBuffer,\n     in which case the only thing that it transports is the @compressionLevel.\n     This can be useful in a pipeline featuring ZSTD_compress_usingCDict() exclusively,\n     expecting a ZSTD_CDict parameter with any data, including those without a known dictionary.",
      "description": "! ZSTD_createCDict() :\n When compressing multiple messages or blocks using the same dictionary,\n it's recommended to digest the dictionary only once, since it's a costly operation.\n ZSTD_createCDict() will create a state from digesting a dictionary.\n The resulting state can be used for future compression operations with very limited startup cost.\n ZSTD_CDict can be created once and shared by multiple threads concurrently, since its usage is read-only.\n@dictBuffer can be released after ZSTD_CDict creation, because its content is copied within CDict.\n Note 1 : Consider experimental function `ZSTD_createCDict_byReference()` if you prefer to not duplicate @dictBuffer content.\n Note 2 : A ZSTD_CDict can be created from an empty @dictBuffer,\n     in which case the only thing that it transports is the @compressionLevel.\n     This can be useful in a pipeline featuring ZSTD_compress_usingCDict() exclusively,\n     expecting a ZSTD_CDict parameter with any data, including those without a known dictionary.",
      "source_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "source_line": 999
    },
    "evidence": {
      "discovered_by": "export_analyzer",
      "header_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "forwarded_to": null,
      "demangled_name": null
    },
    "mcp": {
      "version": "1.0",
      "input_schema": {
        "type": "object",
        "properties": {
          "dictBuffer": {
            "type": "string",
            "description": "void*"
          },
          "dictSize": {
            "type": "integer",
            "description": "size_t"
          },
          "compressionLevel": {
            "type": "integer",
            "description": "int"
          }
        },
        "required": [
          "dictBuffer",
          "dictSize",
          "compressionLevel"
        ]
      },
      "execution": {
        "method": "dll_import",
        "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
        "function_name": "ZSTD_createCDict",
        "calling_convention": "stdcall",
        "charset": "ansi"
      }
    },
    "metadata": {
      "is_signed": false,
      "publisher": null
    },
    "execution": {
      "method": "dll_import",
      "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
      "function_name": "ZSTD_createCDict",
      "calling_convention": "stdcall",
      "charset": "ansi"
    },
    "return_type": "ZSTD_CDict*",
    "parameters": [
      {
        "name": "dictBuffer",
        "type": "const void*",
        "required": true,
        "description": "const void*"
      },
      {
        "name": "dictSize",
        "type": "size_t",
        "required": true,
        "description": "size_t"
      },
      {
        "name": "compressionLevel",
        "type": "int",
        "required": true,
        "description": "int"
      }
    ]
  },
  {
    "name": "ZSTD_createCDict_advanced",
    "tool_id": "dll_ZSTD_createCDict_advanced_79",
    "kind": "export",
    "ordinal": 79,
    "rva": "00010B20",
    "confidence": "guaranteed",
    "confidence_factors": {
      "has_signature": true,
      "has_documentation": false,
      "has_parameters": true,
      "has_return_type": true,
      "is_forwarded": true,
      "is_ordinal_only": false
    },
    "signature": {
      "return_type": "ZSTD_CDict*",
      "parameters": "const void* dict, size_t dictSize, ZSTD_dictLoadMethod_e dictLoadMethod, ZSTD_dictContentType_e dictContentType, ZSTD_compressionParameters cParams, ZSTD_customMem customMem",
      "calling_convention": null,
      "full_prototype": "ZSTDLIB_STATIC_API ZSTD_CDict* ZSTD_createCDict_advanced(const void* dict, size_t dictSize, ZSTD_dictLoadMethod_e dictLoadMethod, ZSTD_dictContentType_e dictContentType, ZSTD_compressionParameters cParams, ZSTD_customMem customMem)"
    },
    "documentation": {
      "summary": "",
      "description": "",
      "source_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "source_line": 1890
    },
    "evidence": {
      "discovered_by": "export_analyzer",
      "header_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "forwarded_to": null,
      "demangled_name": null
    },
    "mcp": {
      "version": "1.0",
      "input_schema": {
        "type": "object",
        "properties": {
          "dict": {
            "type": "string",
            "description": "void*"
          },
          "dictSize": {
            "type": "integer",
            "description": "size_t"
          },
          "dictLoadMethod": {
            "type": "string",
            "description": "ZSTD_dictLoadMethod_e"
          },
          "dictContentType": {
            "type": "string",
            "description": "ZSTD_dictContentType_e"
          },
          "cParams": {
            "type": "string",
            "description": "ZSTD_compressionParameters"
          },
          "customMem": {
            "type": "string",
            "description": "ZSTD_customMem"
          }
        },
        "required": [
          "dict",
          "dictSize",
          "dictLoadMethod",
          "dictContentType",
          "cParams",
          "customMem"
        ]
      },
      "execution": {
        "method": "dll_import",
        "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
        "function_name": "ZSTD_createCDict_advanced",
        "calling_convention": "stdcall",
        "charset": "ansi"
      }
    },
    "metadata": {
      "is_signed": false,
      "publisher": null
    },
    "execution": {
      "method": "dll_import",
      "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
      "function_name": "ZSTD_createCDict_advanced",
      "calling_convention": "stdcall",
      "charset": "ansi"
    },
    "return_type": "ZSTD_CDict*",
    "parameters": [
      {
        "name": "dict",
        "type": "const void*",
        "required": true,
        "description": "const void*"
      },
      {
        "name": "dictSize",
        "type": "size_t",
        "required": true,
        "description": "size_t"
      },
      {
        "name": "dictLoadMethod",
        "type": "ZSTD_dictLoadMethod_e",
        "required": true,
        "description": "ZSTD_dictLoadMethod_e"
      },
      {
        "name": "dictContentType",
        "type": "ZSTD_dictContentType_e",
        "required": true,
        "description": "ZSTD_dictContentType_e"
      },
      {
        "name": "cParams",
        "type": "ZSTD_compressionParameters",
        "required": true,
        "description": "ZSTD_compressionParameters"
      },
      {
        "name": "customMem",
        "type": "ZSTD_customMem",
        "required": true,
        "description": "ZSTD_customMem"
      }
    ]
  },
  {
    "name": "ZSTD_createCDict_advanced2",
    "tool_id": "dll_ZSTD_createCDict_advanced2_80",
    "kind": "export",
    "ordinal": 80,
    "rva": "00010BF0",
    "confidence": "guaranteed",
    "confidence_factors": {
      "has_signature": true,
      "has_documentation": false,
      "has_parameters": true,
      "has_return_type": true,
      "is_forwarded": true,
      "is_ordinal_only": false
    },
    "signature": {
      "return_type": "ZSTD_CDict*",
      "parameters": "const void* dict, size_t dictSize, ZSTD_dictLoadMethod_e dictLoadMethod, ZSTD_dictContentType_e dictContentType, const ZSTD_CCtx_params* cctxParams, ZSTD_customMem customMem",
      "calling_convention": null,
      "full_prototype": "ZSTDLIB_STATIC_API ZSTD_CDict* ZSTD_createCDict_advanced2( const void* dict, size_t dictSize, ZSTD_dictLoadMethod_e dictLoadMethod, ZSTD_dictContentType_e dictContentType, const ZSTD_CCtx_params* cctxParams, ZSTD_customMem customMem)"
    },
    "documentation": {
      "summary": "",
      "description": "",
      "source_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "source_line": 1915
    },
    "evidence": {
      "discovered_by": "export_analyzer",
      "header_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "forwarded_to": null,
      "demangled_name": null
    },
    "mcp": {
      "version": "1.0",
      "input_schema": {
        "type": "object",
        "properties": {
          "dict": {
            "type": "string",
            "description": "void*"
          },
          "dictSize": {
            "type": "integer",
            "description": "size_t"
          },
          "dictLoadMethod": {
            "type": "string",
            "description": "ZSTD_dictLoadMethod_e"
          },
          "dictContentType": {
            "type": "string",
            "description": "ZSTD_dictContentType_e"
          },
          "cctxParams": {
            "type": "string",
            "description": "ZSTD_CCtx_params*"
          },
          "customMem": {
            "type": "string",
            "description": "ZSTD_customMem"
          }
        },
        "required": [
          "dict",
          "dictSize",
          "dictLoadMethod",
          "dictContentType",
          "cctxParams",
          "customMem"
        ]
      },
      "execution": {
        "method": "dll_import",
        "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
        "function_name": "ZSTD_createCDict_advanced2",
        "calling_convention": "stdcall",
        "charset": "ansi"
      }
    },
    "metadata": {
      "is_signed": false,
      "publisher": null
    },
    "execution": {
      "method": "dll_import",
      "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
      "function_name": "ZSTD_createCDict_advanced2",
      "calling_convention": "stdcall",
      "charset": "ansi"
    },
    "return_type": "ZSTD_CDict*",
    "parameters": [
      {
        "name": "dict",
        "type": "const void*",
        "required": true,
        "description": "const void*"
      },
      {
        "name": "dictSize",
        "type": "size_t",
        "required": true,
        "description": "size_t"
      },
      {
        "name": "dictLoadMethod",
        "type": "ZSTD_dictLoadMethod_e",
        "required": true,
        "description": "ZSTD_dictLoadMethod_e"
      },
      {
        "name": "dictContentType",
        "type": "ZSTD_dictContentType_e",
        "required": true,
        "description": "ZSTD_dictContentType_e"
      },
      {
        "name": "cctxParams",
        "type": "const ZSTD_CCtx_params*",
        "required": true,
        "description": "const ZSTD_CCtx_params*"
      },
      {
        "name": "customMem",
        "type": "ZSTD_customMem",
        "required": true,
        "description": "ZSTD_customMem"
      }
    ]
  },
  {
    "name": "ZSTD_createCDict_byReference",
    "tool_id": "dll_ZSTD_createCDict_byReference_81",
    "kind": "export",
    "ordinal": 81,
    "rva": "00011150",
    "confidence": "guaranteed",
    "confidence_factors": {
      "has_signature": true,
      "has_documentation": true,
      "has_parameters": true,
      "has_return_type": true,
      "is_forwarded": true,
      "is_ordinal_only": false
    },
    "signature": {
      "return_type": "ZSTD_CDict*",
      "parameters": "const void* dictBuffer, size_t dictSize, int compressionLevel",
      "calling_convention": null,
      "full_prototype": "ZSTDLIB_STATIC_API ZSTD_CDict* ZSTD_createCDict_byReference(const void* dictBuffer, size_t dictSize, int compressionLevel)"
    },
    "documentation": {
      "summary": "! ZSTD_createCDict_byReference() :\n Create a digested dictionary for compression\n Dictionary content is just referenced, not duplicated.\n As a consequence, `dictBuffer` **must** outlive CDict,\n and its content must remain unmodified throughout the lifetime of CDict.\n note: equivalent to ZSTD_createCDict_advanced(), with dictLoadMethod==ZSTD_dlm_byRef",
      "description": "! ZSTD_createCDict_byReference() :\n Create a digested dictionary for compression\n Dictionary content is just referenced, not duplicated.\n As a consequence, `dictBuffer` **must** outlive CDict,\n and its content must remain unmodified throughout the lifetime of CDict.\n note: equivalent to ZSTD_createCDict_advanced(), with dictLoadMethod==ZSTD_dlm_byRef",
      "source_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "source_line": 1939
    },
    "evidence": {
      "discovered_by": "export_analyzer",
      "header_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "forwarded_to": null,
      "demangled_name": null
    },
    "mcp": {
      "version": "1.0",
      "input_schema": {
        "type": "object",
        "properties": {
          "dictBuffer": {
            "type": "string",
            "description": "void*"
          },
          "dictSize": {
            "type": "integer",
            "description": "size_t"
          },
          "compressionLevel": {
            "type": "integer",
            "description": "int"
          }
        },
        "required": [
          "dictBuffer",
          "dictSize",
          "compressionLevel"
        ]
      },
      "execution": {
        "method": "dll_import",
        "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
        "function_name": "ZSTD_createCDict_byReference",
        "calling_convention": "stdcall",
        "charset": "ansi"
      }
    },
    "metadata": {
      "is_signed": false,
      "publisher": null
    },
    "execution": {
      "method": "dll_import",
      "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
      "function_name": "ZSTD_createCDict_byReference",
      "calling_convention": "stdcall",
      "charset": "ansi"
    },
    "return_type": "ZSTD_CDict*",
    "parameters": [
      {
        "name": "dictBuffer",
        "type": "const void*",
        "required": true,
        "description": "const void*"
      },
      {
        "name": "dictSize",
        "type": "size_t",
        "required": true,
        "description": "size_t"
      },
      {
        "name": "compressionLevel",
        "type": "int",
        "required": true,
        "description": "int"
      }
    ]
  },
  {
    "name": "ZSTD_createCStream",
    "tool_id": "dll_ZSTD_createCStream_82",
    "kind": "export",
    "ordinal": 82,
    "rva": "000114C0",
    "confidence": "guaranteed",
    "confidence_factors": {
      "has_signature": true,
      "has_documentation": false,
      "has_parameters": true,
      "has_return_type": true,
      "is_forwarded": true,
      "is_ordinal_only": false
    },
    "signature": {
      "return_type": "ZSTD_CStream*",
      "parameters": "void",
      "calling_convention": null,
      "full_prototype": "ZSTDLIB_API ZSTD_CStream* ZSTD_createCStream(void)"
    },
    "documentation": {
      "summary": "",
      "description": "",
      "source_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "source_line": 779
    },
    "evidence": {
      "discovered_by": "export_analyzer",
      "header_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "forwarded_to": null,
      "demangled_name": null
    },
    "mcp": {
      "version": "1.0",
      "input_schema": {
        "type": "object",
        "properties": {},
        "required": []
      },
      "execution": {
        "method": "dll_import",
        "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
        "function_name": "ZSTD_createCStream",
        "calling_convention": "stdcall",
        "charset": "ansi"
      }
    },
    "metadata": {
      "is_signed": false,
      "publisher": null
    },
    "execution": {
      "method": "dll_import",
      "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
      "function_name": "ZSTD_createCStream",
      "calling_convention": "stdcall",
      "charset": "ansi"
    },
    "return_type": "ZSTD_CStream*"
  },
  {
    "name": "ZSTD_createCStream_advanced",
    "tool_id": "dll_ZSTD_createCStream_advanced_83",
    "kind": "export",
    "ordinal": 83,
    "rva": "000114F0",
    "confidence": "guaranteed",
    "confidence_factors": {
      "has_signature": true,
      "has_documentation": false,
      "has_parameters": true,
      "has_return_type": true,
      "is_forwarded": true,
      "is_ordinal_only": false
    },
    "signature": {
      "return_type": "ZSTD_CStream*",
      "parameters": "ZSTD_customMem customMem",
      "calling_convention": null,
      "full_prototype": "ZSTDLIB_STATIC_API ZSTD_CStream* ZSTD_createCStream_advanced(ZSTD_customMem customMem)"
    },
    "documentation": {
      "summary": "",
      "description": "",
      "source_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "source_line": 1886
    },
    "evidence": {
      "discovered_by": "export_analyzer",
      "header_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "forwarded_to": null,
      "demangled_name": null
    },
    "mcp": {
      "version": "1.0",
      "input_schema": {
        "type": "object",
        "properties": {
          "customMem": {
            "type": "string",
            "description": "ZSTD_customMem"
          }
        },
        "required": [
          "customMem"
        ]
      },
      "execution": {
        "method": "dll_import",
        "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
        "function_name": "ZSTD_createCStream_advanced",
        "calling_convention": "stdcall",
        "charset": "ansi"
      }
    },
    "metadata": {
      "is_signed": false,
      "publisher": null
    },
    "execution": {
      "method": "dll_import",
      "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
      "function_name": "ZSTD_createCStream_advanced",
      "calling_convention": "stdcall",
      "charset": "ansi"
    },
    "return_type": "ZSTD_CStream*",
    "parameters": [
      {
        "name": "customMem",
        "type": "ZSTD_customMem",
        "required": true,
        "description": "ZSTD_customMem"
      }
    ]
  },
  {
    "name": "ZSTD_createDCtx",
    "tool_id": "dll_ZSTD_createDCtx_84",
    "kind": "export",
    "ordinal": 84,
    "rva": "000655A0",
    "confidence": "guaranteed",
    "confidence_factors": {
      "has_signature": true,
      "has_documentation": false,
      "has_parameters": true,
      "has_return_type": true,
      "is_forwarded": true,
      "is_ordinal_only": false
    },
    "signature": {
      "return_type": "ZSTD_DCtx*",
      "parameters": "void",
      "calling_convention": null,
      "full_prototype": "ZSTDLIB_API ZSTD_DCtx* ZSTD_createDCtx(void)"
    },
    "documentation": {
      "summary": "",
      "description": "",
      "source_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "source_line": 304
    },
    "evidence": {
      "discovered_by": "export_analyzer",
      "header_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "forwarded_to": null,
      "demangled_name": null
    },
    "mcp": {
      "version": "1.0",
      "input_schema": {
        "type": "object",
        "properties": {},
        "required": []
      },
      "execution": {
        "method": "dll_import",
        "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
        "function_name": "ZSTD_createDCtx",
        "calling_convention": "stdcall",
        "charset": "ansi"
      }
    },
    "metadata": {
      "is_signed": false,
      "publisher": null
    },
    "execution": {
      "method": "dll_import",
      "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
      "function_name": "ZSTD_createDCtx",
      "calling_convention": "stdcall",
      "charset": "ansi"
    },
    "return_type": "ZSTD_DCtx*"
  },
  {
    "name": "ZSTD_createDCtx_advanced",
    "tool_id": "dll_ZSTD_createDCtx_advanced_85",
    "kind": "export",
    "ordinal": 85,
    "rva": "000655D0",
    "confidence": "guaranteed",
    "confidence_factors": {
      "has_signature": true,
      "has_documentation": false,
      "has_parameters": true,
      "has_return_type": true,
      "is_forwarded": true,
      "is_ordinal_only": false
    },
    "signature": {
      "return_type": "ZSTD_DCtx*",
      "parameters": "ZSTD_customMem customMem",
      "calling_convention": null,
      "full_prototype": "ZSTDLIB_STATIC_API ZSTD_DCtx* ZSTD_createDCtx_advanced(ZSTD_customMem customMem)"
    },
    "documentation": {
      "summary": "",
      "description": "",
      "source_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "source_line": 1887
    },
    "evidence": {
      "discovered_by": "export_analyzer",
      "header_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "forwarded_to": null,
      "demangled_name": null
    },
    "mcp": {
      "version": "1.0",
      "input_schema": {
        "type": "object",
        "properties": {
          "customMem": {
            "type": "string",
            "description": "ZSTD_customMem"
          }
        },
        "required": [
          "customMem"
        ]
      },
      "execution": {
        "method": "dll_import",
        "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
        "function_name": "ZSTD_createDCtx_advanced",
        "calling_convention": "stdcall",
        "charset": "ansi"
      }
    },
    "metadata": {
      "is_signed": false,
      "publisher": null
    },
    "execution": {
      "method": "dll_import",
      "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
      "function_name": "ZSTD_createDCtx_advanced",
      "calling_convention": "stdcall",
      "charset": "ansi"
    },
    "return_type": "ZSTD_DCtx*",
    "parameters": [
      {
        "name": "customMem",
        "type": "ZSTD_customMem",
        "required": true,
        "description": "ZSTD_customMem"
      }
    ]
  },
  {
    "name": "ZSTD_createDDict",
    "tool_id": "dll_ZSTD_createDDict_86",
    "kind": "export",
    "ordinal": 86,
    "rva": "00064680",
    "confidence": "guaranteed",
    "confidence_factors": {
      "has_signature": true,
      "has_documentation": true,
      "has_parameters": true,
      "has_return_type": true,
      "is_forwarded": true,
      "is_ordinal_only": false
    },
    "signature": {
      "return_type": "ZSTD_DDict*",
      "parameters": "const void* dictBuffer, size_t dictSize",
      "calling_convention": null,
      "full_prototype": "ZSTDLIB_API ZSTD_DDict* ZSTD_createDDict(const void* dictBuffer, size_t dictSize)"
    },
    "documentation": {
      "summary": "! ZSTD_createDDict() :\n Create a digested dictionary, ready to start decompression operation without startup delay.\n dictBuffer can be released after DDict creation, as its content is copied inside DDict.",
      "description": "! ZSTD_createDDict() :\n Create a digested dictionary, ready to start decompression operation without startup delay.\n dictBuffer can be released after DDict creation, as its content is copied inside DDict.",
      "source_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "source_line": 1023
    },
    "evidence": {
      "discovered_by": "export_analyzer",
      "header_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "forwarded_to": null,
      "demangled_name": null
    },
    "mcp": {
      "version": "1.0",
      "input_schema": {
        "type": "object",
        "properties": {
          "dictBuffer": {
            "type": "string",
            "description": "void*"
          },
          "dictSize": {
            "type": "integer",
            "description": "size_t"
          }
        },
        "required": [
          "dictBuffer",
          "dictSize"
        ]
      },
      "execution": {
        "method": "dll_import",
        "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
        "function_name": "ZSTD_createDDict",
        "calling_convention": "stdcall",
        "charset": "ansi"
      }
    },
    "metadata": {
      "is_signed": false,
      "publisher": null
    },
    "execution": {
      "method": "dll_import",
      "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
      "function_name": "ZSTD_createDDict",
      "calling_convention": "stdcall",
      "charset": "ansi"
    },
    "return_type": "ZSTD_DDict*",
    "parameters": [
      {
        "name": "dictBuffer",
        "type": "const void*",
        "required": true,
        "description": "const void*"
      },
      {
        "name": "dictSize",
        "type": "size_t",
        "required": true,
        "description": "size_t"
      }
    ]
  },
  {
    "name": "ZSTD_createDDict_advanced",
    "tool_id": "dll_ZSTD_createDDict_advanced_87",
    "kind": "export",
    "ordinal": 87,
    "rva": "000646B0",
    "confidence": "guaranteed",
    "confidence_factors": {
      "has_signature": true,
      "has_documentation": false,
      "has_parameters": true,
      "has_return_type": true,
      "is_forwarded": true,
      "is_ordinal_only": false
    },
    "signature": {
      "return_type": "ZSTD_DDict*",
      "parameters": "const void* dict, size_t dictSize, ZSTD_dictLoadMethod_e dictLoadMethod, ZSTD_dictContentType_e dictContentType, ZSTD_customMem customMem",
      "calling_convention": null,
      "full_prototype": "ZSTDLIB_STATIC_API ZSTD_DDict* ZSTD_createDDict_advanced( const void* dict, size_t dictSize, ZSTD_dictLoadMethod_e dictLoadMethod, ZSTD_dictContentType_e dictContentType, ZSTD_customMem customMem)"
    },
    "documentation": {
      "summary": "",
      "description": "",
      "source_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "source_line": 1922
    },
    "evidence": {
      "discovered_by": "export_analyzer",
      "header_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "forwarded_to": null,
      "demangled_name": null
    },
    "mcp": {
      "version": "1.0",
      "input_schema": {
        "type": "object",
        "properties": {
          "dict": {
            "type": "string",
            "description": "void*"
          },
          "dictSize": {
            "type": "integer",
            "description": "size_t"
          },
          "dictLoadMethod": {
            "type": "string",
            "description": "ZSTD_dictLoadMethod_e"
          },
          "dictContentType": {
            "type": "string",
            "description": "ZSTD_dictContentType_e"
          },
          "customMem": {
            "type": "string",
            "description": "ZSTD_customMem"
          }
        },
        "required": [
          "dict",
          "dictSize",
          "dictLoadMethod",
          "dictContentType",
          "customMem"
        ]
      },
      "execution": {
        "method": "dll_import",
        "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
        "function_name": "ZSTD_createDDict_advanced",
        "calling_convention": "stdcall",
        "charset": "ansi"
      }
    },
    "metadata": {
      "is_signed": false,
      "publisher": null
    },
    "execution": {
      "method": "dll_import",
      "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
      "function_name": "ZSTD_createDDict_advanced",
      "calling_convention": "stdcall",
      "charset": "ansi"
    },
    "return_type": "ZSTD_DDict*",
    "parameters": [
      {
        "name": "dict",
        "type": "const void*",
        "required": true,
        "description": "const void*"
      },
      {
        "name": "dictSize",
        "type": "size_t",
        "required": true,
        "description": "size_t"
      },
      {
        "name": "dictLoadMethod",
        "type": "ZSTD_dictLoadMethod_e",
        "required": true,
        "description": "ZSTD_dictLoadMethod_e"
      },
      {
        "name": "dictContentType",
        "type": "ZSTD_dictContentType_e",
        "required": true,
        "description": "ZSTD_dictContentType_e"
      },
      {
        "name": "customMem",
        "type": "ZSTD_customMem",
        "required": true,
        "description": "ZSTD_customMem"
      }
    ]
  },
  {
    "name": "ZSTD_createDDict_byReference",
    "tool_id": "dll_ZSTD_createDDict_byReference_88",
    "kind": "export",
    "ordinal": 88,
    "rva": "00064790",
    "confidence": "guaranteed",
    "confidence_factors": {
      "has_signature": true,
      "has_documentation": true,
      "has_parameters": true,
      "has_return_type": true,
      "is_forwarded": true,
      "is_ordinal_only": false
    },
    "signature": {
      "return_type": "ZSTD_DDict*",
      "parameters": "const void* dictBuffer, size_t dictSize",
      "calling_convention": null,
      "full_prototype": "ZSTDLIB_STATIC_API ZSTD_DDict* ZSTD_createDDict_byReference(const void* dictBuffer, size_t dictSize)"
    },
    "documentation": {
      "summary": "! ZSTD_createDDict_byReference() :\n Create a digested dictionary, ready to start decompression operation without startup delay.\n Dictionary content is referenced, and therefore stays in dictBuffer.\n It is important that dictBuffer outlives DDict,\n it must remain read accessible throughout the lifetime of DDict",
      "description": "! ZSTD_createDDict_byReference() :\n Create a digested dictionary, ready to start decompression operation without startup delay.\n Dictionary content is referenced, and therefore stays in dictBuffer.\n It is important that dictBuffer outlives DDict,\n it must remain read accessible throughout the lifetime of DDict",
      "source_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "source_line": 2460
    },
    "evidence": {
      "discovered_by": "export_analyzer",
      "header_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "forwarded_to": null,
      "demangled_name": null
    },
    "mcp": {
      "version": "1.0",
      "input_schema": {
        "type": "object",
        "properties": {
          "dictBuffer": {
            "type": "string",
            "description": "void*"
          },
          "dictSize": {
            "type": "integer",
            "description": "size_t"
          }
        },
        "required": [
          "dictBuffer",
          "dictSize"
        ]
      },
      "execution": {
        "method": "dll_import",
        "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
        "function_name": "ZSTD_createDDict_byReference",
        "calling_convention": "stdcall",
        "charset": "ansi"
      }
    },
    "metadata": {
      "is_signed": false,
      "publisher": null
    },
    "execution": {
      "method": "dll_import",
      "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
      "function_name": "ZSTD_createDDict_byReference",
      "calling_convention": "stdcall",
      "charset": "ansi"
    },
    "return_type": "ZSTD_DDict*",
    "parameters": [
      {
        "name": "dictBuffer",
        "type": "const void*",
        "required": true,
        "description": "const void*"
      },
      {
        "name": "dictSize",
        "type": "size_t",
        "required": true,
        "description": "size_t"
      }
    ]
  },
  {
    "name": "ZSTD_createDStream",
    "tool_id": "dll_ZSTD_createDStream_89",
    "kind": "export",
    "ordinal": 89,
    "rva": "000655A0",
    "confidence": "guaranteed",
    "confidence_factors": {
      "has_signature": true,
      "has_documentation": false,
      "has_parameters": true,
      "has_return_type": true,
      "is_forwarded": true,
      "is_ordinal_only": false
    },
    "signature": {
      "return_type": "ZSTD_DStream*",
      "parameters": "void",
      "calling_convention": null,
      "full_prototype": "ZSTDLIB_API ZSTD_DStream* ZSTD_createDStream(void)"
    },
    "documentation": {
      "summary": "",
      "description": "",
      "source_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "source_line": 911
    },
    "evidence": {
      "discovered_by": "export_analyzer",
      "header_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "forwarded_to": null,
      "demangled_name": null
    },
    "mcp": {
      "version": "1.0",
      "input_schema": {
        "type": "object",
        "properties": {},
        "required": []
      },
      "execution": {
        "method": "dll_import",
        "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
        "function_name": "ZSTD_createDStream",
        "calling_convention": "stdcall",
        "charset": "ansi"
      }
    },
    "metadata": {
      "is_signed": false,
      "publisher": null
    },
    "execution": {
      "method": "dll_import",
      "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
      "function_name": "ZSTD_createDStream",
      "calling_convention": "stdcall",
      "charset": "ansi"
    },
    "return_type": "ZSTD_DStream*"
  },
  {
    "name": "ZSTD_createDStream_advanced",
    "tool_id": "dll_ZSTD_createDStream_advanced_90",
    "kind": "export",
    "ordinal": 90,
    "rva": "000655D0",
    "confidence": "guaranteed",
    "confidence_factors": {
      "has_signature": true,
      "has_documentation": false,
      "has_parameters": true,
      "has_return_type": true,
      "is_forwarded": true,
      "is_ordinal_only": false
    },
    "signature": {
      "return_type": "ZSTD_DStream*",
      "parameters": "ZSTD_customMem customMem",
      "calling_convention": null,
      "full_prototype": "ZSTDLIB_STATIC_API ZSTD_DStream* ZSTD_createDStream_advanced(ZSTD_customMem customMem)"
    },
    "documentation": {
      "summary": "",
      "description": "",
      "source_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "source_line": 1888
    },
    "evidence": {
      "discovered_by": "export_analyzer",
      "header_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "forwarded_to": null,
      "demangled_name": null
    },
    "mcp": {
      "version": "1.0",
      "input_schema": {
        "type": "object",
        "properties": {
          "customMem": {
            "type": "string",
            "description": "ZSTD_customMem"
          }
        },
        "required": [
          "customMem"
        ]
      },
      "execution": {
        "method": "dll_import",
        "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
        "function_name": "ZSTD_createDStream_advanced",
        "calling_convention": "stdcall",
        "charset": "ansi"
      }
    },
    "metadata": {
      "is_signed": false,
      "publisher": null
    },
    "execution": {
      "method": "dll_import",
      "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
      "function_name": "ZSTD_createDStream_advanced",
      "calling_convention": "stdcall",
      "charset": "ansi"
    },
    "return_type": "ZSTD_DStream*",
    "parameters": [
      {
        "name": "customMem",
        "type": "ZSTD_customMem",
        "required": true,
        "description": "ZSTD_customMem"
      }
    ]
  },
  {
    "name": "ZSTD_createThreadPool",
    "tool_id": "dll_ZSTD_createThreadPool_91",
    "kind": "export",
    "ordinal": 91,
    "rva": "00002E00",
    "confidence": "guaranteed",
    "confidence_factors": {
      "has_signature": true,
      "has_documentation": false,
      "has_parameters": true,
      "has_return_type": true,
      "is_forwarded": true,
      "is_ordinal_only": false
    },
    "signature": {
      "return_type": "ZSTD_threadPool*",
      "parameters": "size_t numThreads",
      "calling_convention": null,
      "full_prototype": "ZSTDLIB_STATIC_API ZSTD_threadPool* ZSTD_createThreadPool(size_t numThreads)"
    },
    "documentation": {
      "summary": "",
      "description": "",
      "source_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "source_line": 1907
    },
    "evidence": {
      "discovered_by": "export_analyzer",
      "header_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "forwarded_to": null,
      "demangled_name": null
    },
    "mcp": {
      "version": "1.0",
      "input_schema": {
        "type": "object",
        "properties": {
          "numThreads": {
            "type": "integer",
            "description": "size_t"
          }
        },
        "required": [
          "numThreads"
        ]
      },
      "execution": {
        "method": "dll_import",
        "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
        "function_name": "ZSTD_createThreadPool",
        "calling_convention": "stdcall",
        "charset": "ansi"
      }
    },
    "metadata": {
      "is_signed": false,
      "publisher": null
    },
    "execution": {
      "method": "dll_import",
      "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
      "function_name": "ZSTD_createThreadPool",
      "calling_convention": "stdcall",
      "charset": "ansi"
    },
    "return_type": "ZSTD_threadPool*",
    "parameters": [
      {
        "name": "numThreads",
        "type": "size_t",
        "required": true,
        "description": "size_t"
      }
    ]
  },
  {
    "name": "ZSTD_dParam_getBounds",
    "tool_id": "dll_ZSTD_dParam_getBounds_92",
    "kind": "export",
    "ordinal": 92,
    "rva": "00065710",
    "confidence": "guaranteed",
    "confidence_factors": {
      "has_signature": true,
      "has_documentation": true,
      "has_parameters": true,
      "has_return_type": true,
      "is_forwarded": true,
      "is_ordinal_only": false
    },
    "signature": {
      "return_type": "ZSTD_bounds",
      "parameters": "ZSTD_dParameter dParam",
      "calling_convention": null,
      "full_prototype": "ZSTDLIB_API ZSTD_bounds ZSTD_dParam_getBounds(ZSTD_dParameter dParam)"
    },
    "documentation": {
      "summary": "! ZSTD_dParam_getBounds() :\n All parameters must belong to an interval with lower and upper bounds,\n otherwise they will either trigger an error or be automatically clamped.\n@return : a structure, ZSTD_bounds, which contains\n        - an error status field, which must be tested using ZSTD_isError()\n        - both lower and upper bounds, inclusive",
      "description": "! ZSTD_dParam_getBounds() :\n All parameters must belong to an interval with lower and upper bounds,\n otherwise they will either trigger an error or be automatically clamped.\n@return : a structure, ZSTD_bounds, which contains\n        - an error status field, which must be tested using ZSTD_isError()\n        - both lower and upper bounds, inclusive",
      "source_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "source_line": 677
    },
    "evidence": {
      "discovered_by": "export_analyzer",
      "header_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "forwarded_to": null,
      "demangled_name": null
    },
    "mcp": {
      "version": "1.0",
      "input_schema": {
        "type": "object",
        "properties": {
          "dParam": {
            "type": "string",
            "description": "ZSTD_dParameter"
          }
        },
        "required": [
          "dParam"
        ]
      },
      "execution": {
        "method": "dll_import",
        "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
        "function_name": "ZSTD_dParam_getBounds",
        "calling_convention": "stdcall",
        "charset": "ansi"
      }
    },
    "metadata": {
      "is_signed": false,
      "publisher": null
    },
    "execution": {
      "method": "dll_import",
      "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
      "function_name": "ZSTD_dParam_getBounds",
      "calling_convention": "stdcall",
      "charset": "ansi"
    },
    "return_type": "ZSTD_bounds",
    "parameters": [
      {
        "name": "dParam",
        "type": "ZSTD_dParameter",
        "required": true,
        "description": "ZSTD_dParameter"
      }
    ]
  },
  {
    "name": "ZSTD_decodingBufferSize_min",
    "tool_id": "dll_ZSTD_decodingBufferSize_min_93",
    "kind": "export",
    "ordinal": 93,
    "rva": "00065850",
    "confidence": "guaranteed",
    "confidence_factors": {
      "has_signature": true,
      "has_documentation": true,
      "has_parameters": true,
      "has_return_type": true,
      "is_forwarded": true,
      "is_ordinal_only": false
    },
    "signature": {
      "return_type": "size_t",
      "parameters": "unsigned long long windowSize, unsigned long long frameContentSize",
      "calling_convention": null,
      "full_prototype": "ZSTDLIB_STATIC_API size_t ZSTD_decodingBufferSize_min(unsigned long long windowSize, unsigned long long frameContentSize)"
    },
    "documentation": {
      "summary": "*< when frame content size is not known, pass in frameContentSize == ZSTD_CONTENTSIZE_UNKNOWN",
      "description": "*< when frame content size is not known, pass in frameContentSize == ZSTD_CONTENTSIZE_UNKNOWN",
      "source_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "source_line": 3124
    },
    "evidence": {
      "discovered_by": "export_analyzer",
      "header_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "forwarded_to": null,
      "demangled_name": null
    },
    "mcp": {
      "version": "1.0",
      "input_schema": {
        "type": "object",
        "properties": {
          "windowSize": {
            "type": "integer",
            "description": "unsigned long long"
          },
          "frameContentSize": {
            "type": "integer",
            "description": "unsigned long long"
          }
        },
        "required": [
          "windowSize",
          "frameContentSize"
        ]
      },
      "execution": {
        "method": "dll_import",
        "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
        "function_name": "ZSTD_decodingBufferSize_min",
        "calling_convention": "stdcall",
        "charset": "ansi"
      }
    },
    "metadata": {
      "is_signed": false,
      "publisher": null
    },
    "execution": {
      "method": "dll_import",
      "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
      "function_name": "ZSTD_decodingBufferSize_min",
      "calling_convention": "stdcall",
      "charset": "ansi"
    },
    "return_type": "size_t",
    "parameters": [
      {
        "name": "windowSize",
        "type": "unsigned long long",
        "required": true,
        "description": "unsigned long long"
      },
      {
        "name": "frameContentSize",
        "type": "unsigned long long",
        "required": true,
        "description": "unsigned long long"
      }
    ]
  },
  {
    "name": "ZSTD_decompress",
    "tool_id": "dll_ZSTD_decompress_94",
    "kind": "export",
    "ordinal": 94,
    "rva": "00065880",
    "confidence": "guaranteed",
    "confidence_factors": {
      "has_signature": true,
      "has_documentation": true,
      "has_parameters": true,
      "has_return_type": true,
      "is_forwarded": true,
      "is_ordinal_only": false
    },
    "signature": {
      "return_type": "size_t",
      "parameters": "void* dst, size_t dstCapacity, const void* src, size_t compressedSize",
      "calling_convention": null,
      "full_prototype": "ZSTDLIB_API size_t ZSTD_decompress( void* dst, size_t dstCapacity, const void* src, size_t compressedSize)"
    },
    "documentation": {
      "summary": "! ZSTD_decompress() :\n`compressedSize` : must be the _exact_ size of some number of compressed and/or skippable frames.\n Multiple compressed frames can be decompressed at once with this method.\n The result will be the concatenation of all decompressed frames, back to back.\n`dstCapacity` is an upper bound of originalSize to regenerate.\n First frame's decompressed size can be extracted using ZSTD_getFrameContentSize().\n If maximum upper bound isn't known, prefer using streaming mode to decompress data.\n@return : the number of bytes decompressed into `dst` (<= `dstCapacity`),\n          or an errorCode if it fails (which can be tested using ZSTD_isError()).",
      "description": "! ZSTD_decompress() :\n`compressedSize` : must be the _exact_ size of some number of compressed and/or skippable frames.\n Multiple compressed frames can be decompressed at once with this method.\n The result will be the concatenation of all decompressed frames, back to back.\n`dstCapacity` is an upper bound of originalSize to regenerate.\n First frame's decompressed size can be extracted using ZSTD_getFrameContentSize().\n If maximum upper bound isn't known, prefer using streaming mode to decompress data.\n@return : the number of bytes decompressed into `dst` (<= `dstCapacity`),\n          or an errorCode if it fails (which can be tested using ZSTD_isError()).",
      "source_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "source_line": 173
    },
    "evidence": {
      "discovered_by": "export_analyzer",
      "header_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "forwarded_to": null,
      "demangled_name": null
    },
    "mcp": {
      "version": "1.0",
      "input_schema": {
        "type": "object",
        "properties": {
          "dst": {
            "type": "string",
            "description": "void*"
          },
          "dstCapacity": {
            "type": "integer",
            "description": "size_t"
          },
          "src": {
            "type": "string",
            "description": "void*"
          },
          "compressedSize": {
            "type": "integer",
            "description": "size_t"
          }
        },
        "required": [
          "dst",
          "dstCapacity",
          "src",
          "compressedSize"
        ]
      },
      "execution": {
        "method": "dll_import",
        "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
        "function_name": "ZSTD_decompress",
        "calling_convention": "stdcall",
        "charset": "ansi"
      }
    },
    "metadata": {
      "is_signed": false,
      "publisher": null
    },
    "execution": {
      "method": "dll_import",
      "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
      "function_name": "ZSTD_decompress",
      "calling_convention": "stdcall",
      "charset": "ansi"
    },
    "return_type": "size_t",
    "parameters": [
      {
        "name": "dst",
        "type": "void*",
        "required": true,
        "description": "void*"
      },
      {
        "name": "dstCapacity",
        "type": "size_t",
        "required": true,
        "description": "size_t"
      },
      {
        "name": "src",
        "type": "const void*",
        "required": true,
        "description": "const void*"
      },
      {
        "name": "compressedSize",
        "type": "size_t",
        "required": true,
        "description": "size_t"
      }
    ]
  },
  {
    "name": "ZSTD_decompressBegin",
    "tool_id": "dll_ZSTD_decompressBegin_95",
    "kind": "export",
    "ordinal": 95,
    "rva": "000659B0",
    "confidence": "guaranteed",
    "confidence_factors": {
      "has_signature": true,
      "has_documentation": false,
      "has_parameters": true,
      "has_return_type": true,
      "is_forwarded": true,
      "is_ordinal_only": false
    },
    "signature": {
      "return_type": "size_t",
      "parameters": "ZSTD_DCtx* dctx",
      "calling_convention": null,
      "full_prototype": "ZSTDLIB_STATIC_API size_t ZSTD_decompressBegin(ZSTD_DCtx* dctx)"
    },
    "documentation": {
      "summary": "",
      "description": "",
      "source_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "source_line": 3126
    },
    "evidence": {
      "discovered_by": "export_analyzer",
      "header_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "forwarded_to": null,
      "demangled_name": null
    },
    "mcp": {
      "version": "1.0",
      "input_schema": {
        "type": "object",
        "properties": {
          "dctx": {
            "type": "string",
            "description": "ZSTD_DCtx*"
          }
        },
        "required": [
          "dctx"
        ]
      },
      "execution": {
        "method": "dll_import",
        "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
        "function_name": "ZSTD_decompressBegin",
        "calling_convention": "stdcall",
        "charset": "ansi"
      }
    },
    "metadata": {
      "is_signed": false,
      "publisher": null
    },
    "execution": {
      "method": "dll_import",
      "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
      "function_name": "ZSTD_decompressBegin",
      "calling_convention": "stdcall",
      "charset": "ansi"
    },
    "return_type": "size_t",
    "parameters": [
      {
        "name": "dctx",
        "type": "ZSTD_DCtx*",
        "required": true,
        "description": "ZSTD_DCtx*"
      }
    ]
  },
  {
    "name": "ZSTD_decompressBegin_usingDDict",
    "tool_id": "dll_ZSTD_decompressBegin_usingDDict_96",
    "kind": "export",
    "ordinal": 96,
    "rva": "00065A70",
    "confidence": "guaranteed",
    "confidence_factors": {
      "has_signature": true,
      "has_documentation": false,
      "has_parameters": true,
      "has_return_type": true,
      "is_forwarded": true,
      "is_ordinal_only": false
    },
    "signature": {
      "return_type": "size_t",
      "parameters": "ZSTD_DCtx* dctx, const ZSTD_DDict* ddict",
      "calling_convention": null,
      "full_prototype": "ZSTDLIB_STATIC_API size_t ZSTD_decompressBegin_usingDDict(ZSTD_DCtx* dctx, const ZSTD_DDict* ddict)"
    },
    "documentation": {
      "summary": "",
      "description": "",
      "source_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "source_line": 3128
    },
    "evidence": {
      "discovered_by": "export_analyzer",
      "header_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "forwarded_to": null,
      "demangled_name": null
    },
    "mcp": {
      "version": "1.0",
      "input_schema": {
        "type": "object",
        "properties": {
          "dctx": {
            "type": "string",
            "description": "ZSTD_DCtx*"
          },
          "ddict": {
            "type": "string",
            "description": "ZSTD_DDict*"
          }
        },
        "required": [
          "dctx",
          "ddict"
        ]
      },
      "execution": {
        "method": "dll_import",
        "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
        "function_name": "ZSTD_decompressBegin_usingDDict",
        "calling_convention": "stdcall",
        "charset": "ansi"
      }
    },
    "metadata": {
      "is_signed": false,
      "publisher": null
    },
    "execution": {
      "method": "dll_import",
      "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
      "function_name": "ZSTD_decompressBegin_usingDDict",
      "calling_convention": "stdcall",
      "charset": "ansi"
    },
    "return_type": "size_t",
    "parameters": [
      {
        "name": "dctx",
        "type": "ZSTD_DCtx*",
        "required": true,
        "description": "ZSTD_DCtx*"
      },
      {
        "name": "ddict",
        "type": "const ZSTD_DDict*",
        "required": true,
        "description": "const ZSTD_DDict*"
      }
    ]
  },
  {
    "name": "ZSTD_decompressBegin_usingDict",
    "tool_id": "dll_ZSTD_decompressBegin_usingDict_97",
    "kind": "export",
    "ordinal": 97,
    "rva": "00065BA0",
    "confidence": "guaranteed",
    "confidence_factors": {
      "has_signature": true,
      "has_documentation": false,
      "has_parameters": true,
      "has_return_type": true,
      "is_forwarded": true,
      "is_ordinal_only": false
    },
    "signature": {
      "return_type": "size_t",
      "parameters": "ZSTD_DCtx* dctx, const void* dict, size_t dictSize",
      "calling_convention": null,
      "full_prototype": "ZSTDLIB_STATIC_API size_t ZSTD_decompressBegin_usingDict(ZSTD_DCtx* dctx, const void* dict, size_t dictSize)"
    },
    "documentation": {
      "summary": "",
      "description": "",
      "source_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "source_line": 3127
    },
    "evidence": {
      "discovered_by": "export_analyzer",
      "header_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "forwarded_to": null,
      "demangled_name": null
    },
    "mcp": {
      "version": "1.0",
      "input_schema": {
        "type": "object",
        "properties": {
          "dctx": {
            "type": "string",
            "description": "ZSTD_DCtx*"
          },
          "dict": {
            "type": "string",
            "description": "void*"
          },
          "dictSize": {
            "type": "integer",
            "description": "size_t"
          }
        },
        "required": [
          "dctx",
          "dict",
          "dictSize"
        ]
      },
      "execution": {
        "method": "dll_import",
        "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
        "function_name": "ZSTD_decompressBegin_usingDict",
        "calling_convention": "stdcall",
        "charset": "ansi"
      }
    },
    "metadata": {
      "is_signed": false,
      "publisher": null
    },
    "execution": {
      "method": "dll_import",
      "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
      "function_name": "ZSTD_decompressBegin_usingDict",
      "calling_convention": "stdcall",
      "charset": "ansi"
    },
    "return_type": "size_t",
    "parameters": [
      {
        "name": "dctx",
        "type": "ZSTD_DCtx*",
        "required": true,
        "description": "ZSTD_DCtx*"
      },
      {
        "name": "dict",
        "type": "const void*",
        "required": true,
        "description": "const void*"
      },
      {
        "name": "dictSize",
        "type": "size_t",
        "required": true,
        "description": "size_t"
      }
    ]
  },
  {
    "name": "ZSTD_decompressBlock",
    "tool_id": "dll_ZSTD_decompressBlock_98",
    "kind": "export",
    "ordinal": 98,
    "rva": "0006A010",
    "confidence": "guaranteed",
    "confidence_factors": {
      "has_signature": true,
      "has_documentation": false,
      "has_parameters": true,
      "has_return_type": true,
      "is_forwarded": true,
      "is_ordinal_only": false
    },
    "signature": {
      "return_type": "size_t",
      "parameters": "ZSTD_DCtx* dctx, void* dst, size_t dstCapacity, const void* src, size_t srcSize",
      "calling_convention": null,
      "full_prototype": "ZSTDLIB_STATIC_API size_t ZSTD_decompressBlock(ZSTD_DCtx* dctx, void* dst, size_t dstCapacity, const void* src, size_t srcSize)"
    },
    "documentation": {
      "summary": "",
      "description": "",
      "source_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "source_line": 3190
    },
    "evidence": {
      "discovered_by": "export_analyzer",
      "header_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "forwarded_to": null,
      "demangled_name": null
    },
    "mcp": {
      "version": "1.0",
      "input_schema": {
        "type": "object",
        "properties": {
          "dctx": {
            "type": "string",
            "description": "ZSTD_DCtx*"
          },
          "dst": {
            "type": "string",
            "description": "void*"
          },
          "dstCapacity": {
            "type": "integer",
            "description": "size_t"
          },
          "src": {
            "type": "string",
            "description": "void*"
          },
          "srcSize": {
            "type": "integer",
            "description": "size_t"
          }
        },
        "required": [
          "dctx",
          "dst",
          "dstCapacity",
          "src",
          "srcSize"
        ]
      },
      "execution": {
        "method": "dll_import",
        "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
        "function_name": "ZSTD_decompressBlock",
        "calling_convention": "stdcall",
        "charset": "ansi"
      }
    },
    "metadata": {
      "is_signed": false,
      "publisher": null
    },
    "execution": {
      "method": "dll_import",
      "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
      "function_name": "ZSTD_decompressBlock",
      "calling_convention": "stdcall",
      "charset": "ansi"
    },
    "return_type": "size_t",
    "parameters": [
      {
        "name": "dctx",
        "type": "ZSTD_DCtx*",
        "required": true,
        "description": "ZSTD_DCtx*"
      },
      {
        "name": "dst",
        "type": "void*",
        "required": true,
        "description": "void*"
      },
      {
        "name": "dstCapacity",
        "type": "size_t",
        "required": true,
        "description": "size_t"
      },
      {
        "name": "src",
        "type": "const void*",
        "required": true,
        "description": "const void*"
      },
      {
        "name": "srcSize",
        "type": "size_t",
        "required": true,
        "description": "size_t"
      }
    ]
  },
  {
    "name": "ZSTD_decompressBound",
    "tool_id": "dll_ZSTD_decompressBound_99",
    "kind": "export",
    "ordinal": 99,
    "rva": "00065D20",
    "confidence": "guaranteed",
    "confidence_factors": {
      "has_signature": true,
      "has_documentation": true,
      "has_parameters": true,
      "has_return_type": true,
      "is_forwarded": true,
      "is_ordinal_only": false
    },
    "signature": {
      "return_type": "unsigned long long",
      "parameters": "const void* src, size_t srcSize",
      "calling_convention": null,
      "full_prototype": "ZSTDLIB_STATIC_API unsigned long long ZSTD_decompressBound(const void* src, size_t srcSize)"
    },
    "documentation": {
      "summary": "! ZSTD_decompressBound() :\n `src` should point to the start of a series of ZSTD encoded and/or skippable frames\n `srcSize` must be the _exact_ size of this series\n      (i.e. there should be a frame boundary at `src + srcSize`)\n @return : - upper-bound for the decompressed size of all data in all successive frames\n           - if an error occurred: ZSTD_CONTENTSIZE_ERROR\n\n note 1  : an error can occur if `src` contains an invalid or incorrectly formatted frame.\n note 2  : the upper-bound is exact when the decompressed size field is available in every ZSTD encoded frame of `src`.\n           in this case, `ZSTD_findDecompressedSize` and `ZSTD_decompressBound` return the same value.\n note 3  : when the decompressed size field isn't available, the upper-bound for that frame is calculated by:\n             upper-bound = # blocks * min(128 KB, Window_Size)",
      "description": "! ZSTD_decompressBound() :\n `src` should point to the start of a series of ZSTD encoded and/or skippable frames\n `srcSize` must be the _exact_ size of this series\n      (i.e. there should be a frame boundary at `src + srcSize`)\n @return : - upper-bound for the decompressed size of all data in all successive frames\n           - if an error occurred: ZSTD_CONTENTSIZE_ERROR\n\n note 1  : an error can occur if `src` contains an invalid or incorrectly formatted frame.\n note 2  : the upper-bound is exact when the decompressed size field is available in every ZSTD encoded frame of `src`.\n           in this case, `ZSTD_findDecompressedSize` and `ZSTD_decompressBound` return the same value.\n note 3  : when the decompressed size field isn't available, the upper-bound for that frame is calculated by:\n             upper-bound = # blocks * min(128 KB, Window_Size)",
      "source_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "source_line": 1502
    },
    "evidence": {
      "discovered_by": "export_analyzer",
      "header_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "forwarded_to": null,
      "demangled_name": null
    },
    "mcp": {
      "version": "1.0",
      "input_schema": {
        "type": "object",
        "properties": {
          "src": {
            "type": "string",
            "description": "void*"
          },
          "srcSize": {
            "type": "integer",
            "description": "size_t"
          }
        },
        "required": [
          "src",
          "srcSize"
        ]
      },
      "execution": {
        "method": "dll_import",
        "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
        "function_name": "ZSTD_decompressBound",
        "calling_convention": "stdcall",
        "charset": "ansi"
      }
    },
    "metadata": {
      "is_signed": false,
      "publisher": null
    },
    "execution": {
      "method": "dll_import",
      "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
      "function_name": "ZSTD_decompressBound",
      "calling_convention": "stdcall",
      "charset": "ansi"
    },
    "return_type": "unsigned long long",
    "parameters": [
      {
        "name": "src",
        "type": "const void*",
        "required": true,
        "description": "const void*"
      },
      {
        "name": "srcSize",
        "type": "size_t",
        "required": true,
        "description": "size_t"
      }
    ]
  },
  {
    "name": "ZSTD_decompressContinue",
    "tool_id": "dll_ZSTD_decompressContinue_100",
    "kind": "export",
    "ordinal": 100,
    "rva": "00065F60",
    "confidence": "guaranteed",
    "confidence_factors": {
      "has_signature": true,
      "has_documentation": false,
      "has_parameters": true,
      "has_return_type": true,
      "is_forwarded": true,
      "is_ordinal_only": false
    },
    "signature": {
      "return_type": "size_t",
      "parameters": "ZSTD_DCtx* dctx, void* dst, size_t dstCapacity, const void* src, size_t srcSize",
      "calling_convention": null,
      "full_prototype": "ZSTDLIB_STATIC_API size_t ZSTD_decompressContinue(ZSTD_DCtx* dctx, void* dst, size_t dstCapacity, const void* src, size_t srcSize)"
    },
    "documentation": {
      "summary": "",
      "description": "",
      "source_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "source_line": 3131
    },
    "evidence": {
      "discovered_by": "export_analyzer",
      "header_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "forwarded_to": null,
      "demangled_name": null
    },
    "mcp": {
      "version": "1.0",
      "input_schema": {
        "type": "object",
        "properties": {
          "dctx": {
            "type": "string",
            "description": "ZSTD_DCtx*"
          },
          "dst": {
            "type": "string",
            "description": "void*"
          },
          "dstCapacity": {
            "type": "integer",
            "description": "size_t"
          },
          "src": {
            "type": "string",
            "description": "void*"
          },
          "srcSize": {
            "type": "integer",
            "description": "size_t"
          }
        },
        "required": [
          "dctx",
          "dst",
          "dstCapacity",
          "src",
          "srcSize"
        ]
      },
      "execution": {
        "method": "dll_import",
        "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
        "function_name": "ZSTD_decompressContinue",
        "calling_convention": "stdcall",
        "charset": "ansi"
      }
    },
    "metadata": {
      "is_signed": false,
      "publisher": null
    },
    "execution": {
      "method": "dll_import",
      "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
      "function_name": "ZSTD_decompressContinue",
      "calling_convention": "stdcall",
      "charset": "ansi"
    },
    "return_type": "size_t",
    "parameters": [
      {
        "name": "dctx",
        "type": "ZSTD_DCtx*",
        "required": true,
        "description": "ZSTD_DCtx*"
      },
      {
        "name": "dst",
        "type": "void*",
        "required": true,
        "description": "void*"
      },
      {
        "name": "dstCapacity",
        "type": "size_t",
        "required": true,
        "description": "size_t"
      },
      {
        "name": "src",
        "type": "const void*",
        "required": true,
        "description": "const void*"
      },
      {
        "name": "srcSize",
        "type": "size_t",
        "required": true,
        "description": "size_t"
      }
    ]
  },
  {
    "name": "ZSTD_decompressDCtx",
    "tool_id": "dll_ZSTD_decompressDCtx_101",
    "kind": "export",
    "ordinal": 101,
    "rva": "000663F0",
    "confidence": "guaranteed",
    "confidence_factors": {
      "has_signature": true,
      "has_documentation": true,
      "has_parameters": true,
      "has_return_type": true,
      "is_forwarded": true,
      "is_ordinal_only": false
    },
    "signature": {
      "return_type": "size_t",
      "parameters": "ZSTD_DCtx* dctx, void* dst, size_t dstCapacity, const void* src, size_t srcSize",
      "calling_convention": null,
      "full_prototype": "ZSTDLIB_API size_t ZSTD_decompressDCtx(ZSTD_DCtx* dctx, void* dst, size_t dstCapacity, const void* src, size_t srcSize)"
    },
    "documentation": {
      "summary": "! ZSTD_decompressDCtx() :\n Same as ZSTD_decompress(),\n requires an allocated ZSTD_DCtx.\n Compatible with sticky parameters (see below).",
      "description": "! ZSTD_decompressDCtx() :\n Same as ZSTD_decompress(),\n requires an allocated ZSTD_DCtx.\n Compatible with sticky parameters (see below).",
      "source_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "source_line": 312
    },
    "evidence": {
      "discovered_by": "export_analyzer",
      "header_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "forwarded_to": null,
      "demangled_name": null
    },
    "mcp": {
      "version": "1.0",
      "input_schema": {
        "type": "object",
        "properties": {
          "dctx": {
            "type": "string",
            "description": "ZSTD_DCtx*"
          },
          "dst": {
            "type": "string",
            "description": "void*"
          },
          "dstCapacity": {
            "type": "integer",
            "description": "size_t"
          },
          "src": {
            "type": "string",
            "description": "void*"
          },
          "srcSize": {
            "type": "integer",
            "description": "size_t"
          }
        },
        "required": [
          "dctx",
          "dst",
          "dstCapacity",
          "src",
          "srcSize"
        ]
      },
      "execution": {
        "method": "dll_import",
        "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
        "function_name": "ZSTD_decompressDCtx",
        "calling_convention": "stdcall",
        "charset": "ansi"
      }
    },
    "metadata": {
      "is_signed": false,
      "publisher": null
    },
    "execution": {
      "method": "dll_import",
      "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
      "function_name": "ZSTD_decompressDCtx",
      "calling_convention": "stdcall",
      "charset": "ansi"
    },
    "return_type": "size_t",
    "parameters": [
      {
        "name": "dctx",
        "type": "ZSTD_DCtx*",
        "required": true,
        "description": "ZSTD_DCtx*"
      },
      {
        "name": "dst",
        "type": "void*",
        "required": true,
        "description": "void*"
      },
      {
        "name": "dstCapacity",
        "type": "size_t",
        "required": true,
        "description": "size_t"
      },
      {
        "name": "src",
        "type": "const void*",
        "required": true,
        "description": "const void*"
      },
      {
        "name": "srcSize",
        "type": "size_t",
        "required": true,
        "description": "size_t"
      }
    ]
  },
  {
    "name": "ZSTD_decompressStream",
    "tool_id": "dll_ZSTD_decompressStream_102",
    "kind": "export",
    "ordinal": 102,
    "rva": "00066D30",
    "confidence": "guaranteed",
    "confidence_factors": {
      "has_signature": true,
      "has_documentation": true,
      "has_parameters": true,
      "has_return_type": true,
      "is_forwarded": true,
      "is_ordinal_only": false
    },
    "signature": {
      "return_type": "size_t",
      "parameters": "ZSTD_DStream* zds, ZSTD_outBuffer* output, ZSTD_inBuffer* input",
      "calling_convention": null,
      "full_prototype": "ZSTDLIB_API size_t ZSTD_decompressStream(ZSTD_DStream* zds, ZSTD_outBuffer* output, ZSTD_inBuffer* input)"
    },
    "documentation": {
      "summary": "! ZSTD_decompressStream() :\nStreaming decompression function.\nCall repetitively to consume full input updating it as necessary.\nFunction will update both input and output `pos` fields exposing current state via these fields:\n- `input.pos < input.size`, some input remaining and caller should provide remaining input\n  on the next call.\n- `output.pos < output.size`, decoder flushed internal output buffer.\n- `output.pos == output.size`, unflushed data potentially present in the internal buffers,\n  check ZSTD_decompressStream() @return value,\n  if > 0, invoke it again to flush remaining data to output.\nNote : with no additional input, amount of data flushed <= ZSTD_BLOCKSIZE_MAX.\n\n@return : 0 when a frame is completely decoded and fully flushed,\n          or an error code, which can be tested using ZSTD_isError(),\n          or any other value > 0, which means there is some decoding or flushing to do to complete current frame.\n\nNote: when an operation returns with an error code, the @zds state may be left in undefined state.\n      It's UB to invoke `ZSTD_decompressStream()` on such a state.\n      In order to re-use such a state, it must be first reset,\n      which can be done explicitly (`ZSTD_DCtx_reset()`),\n      or is implied for operations starting some new decompression job (`ZSTD_initDStream`, `ZSTD_decompressDCtx()`, `ZSTD_decompress_usingDict()`)",
      "description": "! ZSTD_decompressStream() :\nStreaming decompression function.\nCall repetitively to consume full input updating it as necessary.\nFunction will update both input and output `pos` fields exposing current state via these fields:\n- `input.pos < input.size`, some input remaining and caller should provide remaining input\n  on the next call.\n- `output.pos < output.size`, decoder flushed internal output buffer.\n- `output.pos == output.size`, unflushed data potentially present in the internal buffers,\n  check ZSTD_decompressStream() @return value,\n  if > 0, invoke it again to flush remaining data to output.\nNote : with no additional input, amount of data flushed <= ZSTD_BLOCKSIZE_MAX.\n\n@return : 0 when a frame is completely decoded and fully flushed,\n          or an error code, which can be tested using ZSTD_isError(),\n          or any other value > 0, which means there is some decoding or flushing to do to complete current frame.\n\nNote: when an operation returns with an error code, the @zds state may be left in undefined state.\n      It's UB to invoke `ZSTD_decompressStream()` on such a state.\n      In order to re-use such a state, it must be first reset,\n      which can be done explicitly (`ZSTD_DCtx_reset()`),\n      or is implied for operations starting some new decompression job (`ZSTD_initDStream`, `ZSTD_decompressDCtx()`, `ZSTD_decompress_usingDict()`)",
      "source_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "source_line": 948
    },
    "evidence": {
      "discovered_by": "export_analyzer",
      "header_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "forwarded_to": null,
      "demangled_name": null
    },
    "mcp": {
      "version": "1.0",
      "input_schema": {
        "type": "object",
        "properties": {
          "zds": {
            "type": "string",
            "description": "ZSTD_DStream*"
          },
          "output": {
            "type": "string",
            "description": "ZSTD_outBuffer*"
          },
          "input": {
            "type": "string",
            "description": "ZSTD_inBuffer*"
          }
        },
        "required": [
          "zds",
          "output",
          "input"
        ]
      },
      "execution": {
        "method": "dll_import",
        "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
        "function_name": "ZSTD_decompressStream",
        "calling_convention": "stdcall",
        "charset": "ansi"
      }
    },
    "metadata": {
      "is_signed": false,
      "publisher": null
    },
    "execution": {
      "method": "dll_import",
      "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
      "function_name": "ZSTD_decompressStream",
      "calling_convention": "stdcall",
      "charset": "ansi"
    },
    "return_type": "size_t",
    "parameters": [
      {
        "name": "zds",
        "type": "ZSTD_DStream*",
        "required": true,
        "description": "ZSTD_DStream*"
      },
      {
        "name": "output",
        "type": "ZSTD_outBuffer*",
        "required": true,
        "description": "ZSTD_outBuffer*"
      },
      {
        "name": "input",
        "type": "ZSTD_inBuffer*",
        "required": true,
        "description": "ZSTD_inBuffer*"
      }
    ]
  },
  {
    "name": "ZSTD_decompressStream_simpleArgs",
    "tool_id": "dll_ZSTD_decompressStream_simpleArgs_103",
    "kind": "export",
    "ordinal": 103,
    "rva": "00067C90",
    "confidence": "guaranteed",
    "confidence_factors": {
      "has_signature": true,
      "has_documentation": true,
      "has_parameters": true,
      "has_return_type": true,
      "is_forwarded": true,
      "is_ordinal_only": false
    },
    "signature": {
      "return_type": "size_t",
      "parameters": "ZSTD_DCtx* dctx, void* dst, size_t dstCapacity, size_t* dstPos, const void* src, size_t srcSize, size_t* srcPos",
      "calling_convention": null,
      "full_prototype": "ZSTDLIB_STATIC_API size_t ZSTD_decompressStream_simpleArgs ( ZSTD_DCtx* dctx, void* dst, size_t dstCapacity, size_t* dstPos, const void* src, size_t srcSize, size_t* srcPos)"
    },
    "documentation": {
      "summary": "! ZSTD_decompressStream_simpleArgs() :\n Same as ZSTD_decompressStream(),\n but using only integral types as arguments.\n This can be helpful for binders from dynamic languages\n which have troubles handling structures containing memory pointers.",
      "description": "! ZSTD_decompressStream_simpleArgs() :\n Same as ZSTD_decompressStream(),\n but using only integral types as arguments.\n This can be helpful for binders from dynamic languages\n which have troubles handling structures containing memory pointers.",
      "source_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "source_line": 2612
    },
    "evidence": {
      "discovered_by": "export_analyzer",
      "header_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "forwarded_to": null,
      "demangled_name": null
    },
    "mcp": {
      "version": "1.0",
      "input_schema": {
        "type": "object",
        "properties": {
          "dctx": {
            "type": "string",
            "description": "ZSTD_DCtx*"
          },
          "dst": {
            "type": "string",
            "description": "void*"
          },
          "dstCapacity": {
            "type": "integer",
            "description": "size_t"
          },
          "dstPos": {
            "type": "string",
            "description": "size_t*"
          },
          "src": {
            "type": "string",
            "description": "void*"
          },
          "srcSize": {
            "type": "integer",
            "description": "size_t"
          },
          "srcPos": {
            "type": "string",
            "description": "size_t*"
          }
        },
        "required": [
          "dctx",
          "dst",
          "dstCapacity",
          "dstPos",
          "src",
          "srcSize",
          "srcPos"
        ]
      },
      "execution": {
        "method": "dll_import",
        "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
        "function_name": "ZSTD_decompressStream_simpleArgs",
        "calling_convention": "stdcall",
        "charset": "ansi"
      }
    },
    "metadata": {
      "is_signed": false,
      "publisher": null
    },
    "execution": {
      "method": "dll_import",
      "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
      "function_name": "ZSTD_decompressStream_simpleArgs",
      "calling_convention": "stdcall",
      "charset": "ansi"
    },
    "return_type": "size_t",
    "parameters": [
      {
        "name": "dctx",
        "type": "ZSTD_DCtx*",
        "required": true,
        "description": "ZSTD_DCtx*"
      },
      {
        "name": "dst",
        "type": "void*",
        "required": true,
        "description": "void*"
      },
      {
        "name": "dstCapacity",
        "type": "size_t",
        "required": true,
        "description": "size_t"
      },
      {
        "name": "dstPos",
        "type": "size_t*",
        "required": true,
        "description": "size_t*"
      },
      {
        "name": "src",
        "type": "const void*",
        "required": true,
        "description": "const void*"
      },
      {
        "name": "srcSize",
        "type": "size_t",
        "required": true,
        "description": "size_t"
      },
      {
        "name": "srcPos",
        "type": "size_t*",
        "required": true,
        "description": "size_t*"
      }
    ]
  },
  {
    "name": "ZSTD_decompress_usingDDict",
    "tool_id": "dll_ZSTD_decompress_usingDDict_104",
    "kind": "export",
    "ordinal": 104,
    "rva": "00067D00",
    "confidence": "guaranteed",
    "confidence_factors": {
      "has_signature": true,
      "has_documentation": true,
      "has_parameters": true,
      "has_return_type": true,
      "is_forwarded": true,
      "is_ordinal_only": false
    },
    "signature": {
      "return_type": "size_t",
      "parameters": "ZSTD_DCtx* dctx, void* dst, size_t dstCapacity, const void* src, size_t srcSize, const ZSTD_DDict* ddict",
      "calling_convention": null,
      "full_prototype": "ZSTDLIB_API size_t ZSTD_decompress_usingDDict(ZSTD_DCtx* dctx, void* dst, size_t dstCapacity, const void* src, size_t srcSize, const ZSTD_DDict* ddict)"
    },
    "documentation": {
      "summary": "! ZSTD_decompress_usingDDict() :\n Decompression using a digested Dictionary.\n Recommended when same dictionary is used multiple times.",
      "description": "! ZSTD_decompress_usingDDict() :\n Decompression using a digested Dictionary.\n Recommended when same dictionary is used multiple times.",
      "source_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "source_line": 1033
    },
    "evidence": {
      "discovered_by": "export_analyzer",
      "header_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "forwarded_to": null,
      "demangled_name": null
    },
    "mcp": {
      "version": "1.0",
      "input_schema": {
        "type": "object",
        "properties": {
          "dctx": {
            "type": "string",
            "description": "ZSTD_DCtx*"
          },
          "dst": {
            "type": "string",
            "description": "void*"
          },
          "dstCapacity": {
            "type": "integer",
            "description": "size_t"
          },
          "src": {
            "type": "string",
            "description": "void*"
          },
          "srcSize": {
            "type": "integer",
            "description": "size_t"
          },
          "ddict": {
            "type": "string",
            "description": "ZSTD_DDict*"
          }
        },
        "required": [
          "dctx",
          "dst",
          "dstCapacity",
          "src",
          "srcSize",
          "ddict"
        ]
      },
      "execution": {
        "method": "dll_import",
        "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
        "function_name": "ZSTD_decompress_usingDDict",
        "calling_convention": "stdcall",
        "charset": "ansi"
      }
    },
    "metadata": {
      "is_signed": false,
      "publisher": null
    },
    "execution": {
      "method": "dll_import",
      "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
      "function_name": "ZSTD_decompress_usingDDict",
      "calling_convention": "stdcall",
      "charset": "ansi"
    },
    "return_type": "size_t",
    "parameters": [
      {
        "name": "dctx",
        "type": "ZSTD_DCtx*",
        "required": true,
        "description": "ZSTD_DCtx*"
      },
      {
        "name": "dst",
        "type": "void*",
        "required": true,
        "description": "void*"
      },
      {
        "name": "dstCapacity",
        "type": "size_t",
        "required": true,
        "description": "size_t"
      },
      {
        "name": "src",
        "type": "const void*",
        "required": true,
        "description": "const void*"
      },
      {
        "name": "srcSize",
        "type": "size_t",
        "required": true,
        "description": "size_t"
      },
      {
        "name": "ddict",
        "type": "const ZSTD_DDict*",
        "required": true,
        "description": "const ZSTD_DDict*"
      }
    ]
  },
  {
    "name": "ZSTD_decompress_usingDict",
    "tool_id": "dll_ZSTD_decompress_usingDict_105",
    "kind": "export",
    "ordinal": 105,
    "rva": "00067D30",
    "confidence": "guaranteed",
    "confidence_factors": {
      "has_signature": true,
      "has_documentation": true,
      "has_parameters": true,
      "has_return_type": true,
      "is_forwarded": true,
      "is_ordinal_only": false
    },
    "signature": {
      "return_type": "size_t",
      "parameters": "ZSTD_DCtx* dctx, void* dst, size_t dstCapacity, const void* src, size_t srcSize, const void* dict,size_t dictSize",
      "calling_convention": null,
      "full_prototype": "ZSTDLIB_API size_t ZSTD_decompress_usingDict(ZSTD_DCtx* dctx, void* dst, size_t dstCapacity, const void* src, size_t srcSize, const void* dict,size_t dictSize)"
    },
    "documentation": {
      "summary": "! ZSTD_decompress_usingDict() :\n Decompression using a known Dictionary.\n Dictionary must be identical to the one used during compression.\n Note : This function loads the dictionary, resulting in significant startup delay.\n        It's intended for a dictionary used only once.\n Note : When `dict == NULL || dictSize < 8` no dictionary is used.",
      "description": "! ZSTD_decompress_usingDict() :\n Decompression using a known Dictionary.\n Dictionary must be identical to the one used during compression.\n Note : This function loads the dictionary, resulting in significant startup delay.\n        It's intended for a dictionary used only once.\n Note : When `dict == NULL || dictSize < 8` no dictionary is used.",
      "source_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "source_line": 976
    },
    "evidence": {
      "discovered_by": "export_analyzer",
      "header_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "forwarded_to": null,
      "demangled_name": null
    },
    "mcp": {
      "version": "1.0",
      "input_schema": {
        "type": "object",
        "properties": {
          "dctx": {
            "type": "string",
            "description": "ZSTD_DCtx*"
          },
          "dst": {
            "type": "string",
            "description": "void*"
          },
          "dstCapacity": {
            "type": "integer",
            "description": "size_t"
          },
          "src": {
            "type": "string",
            "description": "void*"
          },
          "srcSize": {
            "type": "integer",
            "description": "size_t"
          },
          "dict": {
            "type": "string",
            "description": "void*"
          },
          "dictSize": {
            "type": "integer",
            "description": "size_t"
          }
        },
        "required": [
          "dctx",
          "dst",
          "dstCapacity",
          "src",
          "srcSize",
          "dict",
          "dictSize"
        ]
      },
      "execution": {
        "method": "dll_import",
        "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
        "function_name": "ZSTD_decompress_usingDict",
        "calling_convention": "stdcall",
        "charset": "ansi"
      }
    },
    "metadata": {
      "is_signed": false,
      "publisher": null
    },
    "execution": {
      "method": "dll_import",
      "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
      "function_name": "ZSTD_decompress_usingDict",
      "calling_convention": "stdcall",
      "charset": "ansi"
    },
    "return_type": "size_t",
    "parameters": [
      {
        "name": "dctx",
        "type": "ZSTD_DCtx*",
        "required": true,
        "description": "ZSTD_DCtx*"
      },
      {
        "name": "dst",
        "type": "void*",
        "required": true,
        "description": "void*"
      },
      {
        "name": "dstCapacity",
        "type": "size_t",
        "required": true,
        "description": "size_t"
      },
      {
        "name": "src",
        "type": "const void*",
        "required": true,
        "description": "const void*"
      },
      {
        "name": "srcSize",
        "type": "size_t",
        "required": true,
        "description": "size_t"
      },
      {
        "name": "dict",
        "type": "const void*",
        "required": true,
        "description": "const void*"
      },
      {
        "name": "dictSize",
        "type": "size_t",
        "required": true,
        "description": "size_t"
      }
    ]
  },
  {
    "name": "ZSTD_decompressionMargin",
    "tool_id": "dll_ZSTD_decompressionMargin_106",
    "kind": "export",
    "ordinal": 106,
    "rva": "00067D70",
    "confidence": "guaranteed",
    "confidence_factors": {
      "has_signature": true,
      "has_documentation": true,
      "has_parameters": true,
      "has_return_type": true,
      "is_forwarded": true,
      "is_ordinal_only": false
    },
    "signature": {
      "return_type": "size_t",
      "parameters": "const void* src, size_t srcSize",
      "calling_convention": null,
      "full_prototype": "ZSTDLIB_STATIC_API size_t ZSTD_decompressionMargin(const void* src, size_t srcSize)"
    },
    "documentation": {
      "summary": "! ZSTD_decompressionMargin() :\nZstd supports in-place decompression, where the input and output buffers overlap.\nIn this case, the output buffer must be at least (Margin + Output_Size) bytes large,\nand the input buffer must be at the end of the output buffer.\n\n _______________________ Output Buffer ________________________\n|                                                              |\n|                                        ____ Input Buffer ____|\n|                                       |                      |\nv                                       v                      v\n|---------------------------------------|-----------|----------|\n^                                                   ^          ^\n|___________________ Output_Size ___________________|_ Margin _|\n\nNOTE: See also ZSTD_DECOMPRESSION_MARGIN().\nNOTE: This applies only to single-pass decompression through ZSTD_decompress() or\nZSTD_decompressDCtx().\nNOTE: This function supports multi-frame input.\n\n@param src The compressed frame(s)\n@param srcSize The size of the compressed frame(s)\n@returns The decompression margin or an error that can be checked with ZSTD_isError().",
      "description": "! ZSTD_decompressionMargin() :\nZstd supports in-place decompression, where the input and output buffers overlap.\nIn this case, the output buffer must be at least (Margin + Output_Size) bytes large,\nand the input buffer must be at the end of the output buffer.\n\n _______________________ Output Buffer ________________________\n|                                                              |\n|                                        ____ Input Buffer ____|\n|                                       |                      |\nv                                       v                      v\n|---------------------------------------|-----------|----------|\n^                                                   ^          ^\n|___________________ Output_Size ___________________|_ Margin _|\n\nNOTE: See also ZSTD_DECOMPRESSION_MARGIN().\nNOTE: This applies only to single-pass decompression through ZSTD_decompress() or\nZSTD_decompressDCtx().\nNOTE: This function supports multi-frame input.\n\n@param src The compressed frame(s)\n@param srcSize The size of the compressed frame(s)\n@returns The decompression margin or an error that can be checked with ZSTD_isError().",
      "source_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "source_line": 1559
    },
    "evidence": {
      "discovered_by": "export_analyzer",
      "header_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "forwarded_to": null,
      "demangled_name": null
    },
    "mcp": {
      "version": "1.0",
      "input_schema": {
        "type": "object",
        "properties": {
          "src": {
            "type": "string",
            "description": "void*"
          },
          "srcSize": {
            "type": "integer",
            "description": "size_t"
          }
        },
        "required": [
          "src",
          "srcSize"
        ]
      },
      "execution": {
        "method": "dll_import",
        "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
        "function_name": "ZSTD_decompressionMargin",
        "calling_convention": "stdcall",
        "charset": "ansi"
      }
    },
    "metadata": {
      "is_signed": false,
      "publisher": null
    },
    "execution": {
      "method": "dll_import",
      "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
      "function_name": "ZSTD_decompressionMargin",
      "calling_convention": "stdcall",
      "charset": "ansi"
    },
    "return_type": "size_t",
    "parameters": [
      {
        "name": "src",
        "type": "const void*",
        "required": true,
        "description": "const void*"
      },
      {
        "name": "srcSize",
        "type": "size_t",
        "required": true,
        "description": "size_t"
      }
    ]
  },
  {
    "name": "ZSTD_defaultCLevel",
    "tool_id": "dll_ZSTD_defaultCLevel_107",
    "kind": "export",
    "ordinal": 107,
    "rva": "00011990",
    "confidence": "guaranteed",
    "confidence_factors": {
      "has_signature": true,
      "has_documentation": true,
      "has_parameters": true,
      "has_return_type": true,
      "is_forwarded": true,
      "is_ordinal_only": false
    },
    "signature": {
      "return_type": "int",
      "parameters": "void",
      "calling_convention": null,
      "full_prototype": "ZSTDLIB_API int ZSTD_defaultCLevel(void)"
    },
    "documentation": {
      "summary": "!< default compression level, specified by ZSTD_CLEVEL_DEFAULT, requires v1.5.0+",
      "description": "!< default compression level, specified by ZSTD_CLEVEL_DEFAULT, requires v1.5.0+",
      "source_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "source_line": 264
    },
    "evidence": {
      "discovered_by": "export_analyzer",
      "header_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "forwarded_to": null,
      "demangled_name": null
    },
    "mcp": {
      "version": "1.0",
      "input_schema": {
        "type": "object",
        "properties": {},
        "required": []
      },
      "execution": {
        "method": "dll_import",
        "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
        "function_name": "ZSTD_defaultCLevel",
        "calling_convention": "stdcall",
        "charset": "ansi"
      }
    },
    "metadata": {
      "is_signed": false,
      "publisher": null
    },
    "execution": {
      "method": "dll_import",
      "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
      "function_name": "ZSTD_defaultCLevel",
      "calling_convention": "stdcall",
      "charset": "ansi"
    },
    "return_type": "int"
  },
  {
    "name": "ZSTD_endStream",
    "tool_id": "dll_ZSTD_endStream_108",
    "kind": "export",
    "ordinal": 108,
    "rva": "00011C70",
    "confidence": "guaranteed",
    "confidence_factors": {
      "has_signature": true,
      "has_documentation": true,
      "has_parameters": true,
      "has_return_type": true,
      "is_forwarded": true,
      "is_ordinal_only": false
    },
    "signature": {
      "return_type": "size_t",
      "parameters": "ZSTD_CStream* zcs, ZSTD_outBuffer* output",
      "calling_convention": null,
      "full_prototype": "ZSTDLIB_API size_t ZSTD_endStream(ZSTD_CStream* zcs, ZSTD_outBuffer* output)"
    },
    "documentation": {
      "summary": "! Equivalent to ZSTD_compressStream2(zcs, output, &emptyInput, ZSTD_e_end).",
      "description": "! Equivalent to ZSTD_compressStream2(zcs, output, &emptyInput, ZSTD_e_end).",
      "source_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "source_line": 873
    },
    "evidence": {
      "discovered_by": "export_analyzer",
      "header_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "forwarded_to": null,
      "demangled_name": null
    },
    "mcp": {
      "version": "1.0",
      "input_schema": {
        "type": "object",
        "properties": {
          "zcs": {
            "type": "string",
            "description": "ZSTD_CStream*"
          },
          "output": {
            "type": "string",
            "description": "ZSTD_outBuffer*"
          }
        },
        "required": [
          "zcs",
          "output"
        ]
      },
      "execution": {
        "method": "dll_import",
        "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
        "function_name": "ZSTD_endStream",
        "calling_convention": "stdcall",
        "charset": "ansi"
      }
    },
    "metadata": {
      "is_signed": false,
      "publisher": null
    },
    "execution": {
      "method": "dll_import",
      "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
      "function_name": "ZSTD_endStream",
      "calling_convention": "stdcall",
      "charset": "ansi"
    },
    "return_type": "size_t",
    "parameters": [
      {
        "name": "zcs",
        "type": "ZSTD_CStream*",
        "required": true,
        "description": "ZSTD_CStream*"
      },
      {
        "name": "output",
        "type": "ZSTD_outBuffer*",
        "required": true,
        "description": "ZSTD_outBuffer*"
      }
    ]
  },
  {
    "name": "ZSTD_estimateCCtxSize",
    "tool_id": "dll_ZSTD_estimateCCtxSize_109",
    "kind": "export",
    "ordinal": 109,
    "rva": "00012460",
    "confidence": "guaranteed",
    "confidence_factors": {
      "has_signature": true,
      "has_documentation": true,
      "has_parameters": true,
      "has_return_type": true,
      "is_forwarded": true,
      "is_ordinal_only": false
    },
    "signature": {
      "return_type": "size_t",
      "parameters": "int maxCompressionLevel",
      "calling_convention": null,
      "full_prototype": "ZSTDLIB_STATIC_API size_t ZSTD_estimateCCtxSize(int maxCompressionLevel)"
    },
    "documentation": {
      "summary": "! ZSTD_estimate*() :\n These functions make it possible to estimate memory usage\n of a future {D,C}Ctx, before its creation.\n This is useful in combination with ZSTD_initStatic(),\n which makes it possible to employ a static buffer for ZSTD_CCtx* state.\n\n ZSTD_estimateCCtxSize() will provide a memory budget large enough\n to compress data of any size using one-shot compression ZSTD_compressCCtx() or ZSTD_compress2()\n associated with any compression level up to max specified one.\n The estimate will assume the input may be arbitrarily large,\n which is the worst case.\n\n Note that the size estimation is specific for one-shot compression,\n it is not valid for streaming (see ZSTD_estimateCStreamSize*())\n nor other potential ways of using a ZSTD_CCtx* state.\n\n When srcSize can be bound by a known and rather \"small\" value,\n this knowledge can be used to provide a tighter budget estimation\n because the ZSTD_CCtx* state will need less memory for small inputs.\n This tighter estimation can be provided by employing more advanced functions\n ZSTD_estimateCCtxSize_usingCParams(), which can be used in tandem with ZSTD_getCParams(),\n and ZSTD_estimateCCtxSize_usingCCtxParams(), which can be used in tandem with ZSTD_CCtxParams_setParameter().\n Both can be used to estimate memory using custom compression parameters and arbitrary srcSize limits.\n\n Note : only single-threaded compression is supported.\n ZSTD_estimateCCtxSize_usingCCtxParams() will return an error code if ZSTD_c_nbWorkers is >= 1.",
      "description": "! ZSTD_estimate*() :\n These functions make it possible to estimate memory usage\n of a future {D,C}Ctx, before its creation.\n This is useful in combination with ZSTD_initStatic(),\n which makes it possible to employ a static buffer for ZSTD_CCtx* state.\n\n ZSTD_estimateCCtxSize() will provide a memory budget large enough\n to compress data of any size using one-shot compression ZSTD_compressCCtx() or ZSTD_compress2()\n associated with any compression level up to max specified one.\n The estimate will assume the input may be arbitrarily large,\n which is the worst case.\n\n Note that the size estimation is specific for one-shot compression,\n it is not valid for streaming (see ZSTD_estimateCStreamSize*())\n nor other potential ways of using a ZSTD_CCtx* state.\n\n When srcSize can be bound by a known and rather \"small\" value,\n this knowledge can be used to provide a tighter budget estimation\n because the ZSTD_CCtx* state will need less memory for small inputs.\n This tighter estimation can be provided by employing more advanced functions\n ZSTD_estimateCCtxSize_usingCParams(), which can be used in tandem with ZSTD_getCParams(),\n and ZSTD_estimateCCtxSize_usingCCtxParams(), which can be used in tandem with ZSTD_CCtxParams_setParameter().\n Both can be used to estimate memory using custom compression parameters and arbitrary srcSize limits.\n\n Note : only single-threaded compression is supported.\n ZSTD_estimateCCtxSize_usingCCtxParams() will return an error code if ZSTD_c_nbWorkers is >= 1.",
      "source_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "source_line": 1782
    },
    "evidence": {
      "discovered_by": "export_analyzer",
      "header_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "forwarded_to": null,
      "demangled_name": null
    },
    "mcp": {
      "version": "1.0",
      "input_schema": {
        "type": "object",
        "properties": {
          "maxCompressionLevel": {
            "type": "integer",
            "description": "int"
          }
        },
        "required": [
          "maxCompressionLevel"
        ]
      },
      "execution": {
        "method": "dll_import",
        "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
        "function_name": "ZSTD_estimateCCtxSize",
        "calling_convention": "stdcall",
        "charset": "ansi"
      }
    },
    "metadata": {
      "is_signed": false,
      "publisher": null
    },
    "execution": {
      "method": "dll_import",
      "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
      "function_name": "ZSTD_estimateCCtxSize",
      "calling_convention": "stdcall",
      "charset": "ansi"
    },
    "return_type": "size_t",
    "parameters": [
      {
        "name": "maxCompressionLevel",
        "type": "int",
        "required": true,
        "description": "int"
      }
    ]
  },
  {
    "name": "ZSTD_estimateCCtxSize_usingCCtxParams",
    "tool_id": "dll_ZSTD_estimateCCtxSize_usingCCtxParams_110",
    "kind": "export",
    "ordinal": 110,
    "rva": "00012590",
    "confidence": "guaranteed",
    "confidence_factors": {
      "has_signature": true,
      "has_documentation": false,
      "has_parameters": true,
      "has_return_type": true,
      "is_forwarded": true,
      "is_ordinal_only": false
    },
    "signature": {
      "return_type": "size_t",
      "parameters": "const ZSTD_CCtx_params* params",
      "calling_convention": null,
      "full_prototype": "ZSTDLIB_STATIC_API size_t ZSTD_estimateCCtxSize_usingCCtxParams(const ZSTD_CCtx_params* params)"
    },
    "documentation": {
      "summary": "",
      "description": "",
      "source_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "source_line": 1784
    },
    "evidence": {
      "discovered_by": "export_analyzer",
      "header_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "forwarded_to": null,
      "demangled_name": null
    },
    "mcp": {
      "version": "1.0",
      "input_schema": {
        "type": "object",
        "properties": {
          "params": {
            "type": "string",
            "description": "ZSTD_CCtx_params*"
          }
        },
        "required": [
          "params"
        ]
      },
      "execution": {
        "method": "dll_import",
        "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
        "function_name": "ZSTD_estimateCCtxSize_usingCCtxParams",
        "calling_convention": "stdcall",
        "charset": "ansi"
      }
    },
    "metadata": {
      "is_signed": false,
      "publisher": null
    },
    "execution": {
      "method": "dll_import",
      "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
      "function_name": "ZSTD_estimateCCtxSize_usingCCtxParams",
      "calling_convention": "stdcall",
      "charset": "ansi"
    },
    "return_type": "size_t",
    "parameters": [
      {
        "name": "params",
        "type": "const ZSTD_CCtx_params*",
        "required": true,
        "description": "const ZSTD_CCtx_params*"
      }
    ]
  },
  {
    "name": "ZSTD_estimateCCtxSize_usingCParams",
    "tool_id": "dll_ZSTD_estimateCCtxSize_usingCParams_111",
    "kind": "export",
    "ordinal": 111,
    "rva": "00012890",
    "confidence": "guaranteed",
    "confidence_factors": {
      "has_signature": true,
      "has_documentation": false,
      "has_parameters": true,
      "has_return_type": true,
      "is_forwarded": true,
      "is_ordinal_only": false
    },
    "signature": {
      "return_type": "size_t",
      "parameters": "ZSTD_compressionParameters cParams",
      "calling_convention": null,
      "full_prototype": "ZSTDLIB_STATIC_API size_t ZSTD_estimateCCtxSize_usingCParams(ZSTD_compressionParameters cParams)"
    },
    "documentation": {
      "summary": "",
      "description": "",
      "source_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "source_line": 1783
    },
    "evidence": {
      "discovered_by": "export_analyzer",
      "header_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "forwarded_to": null,
      "demangled_name": null
    },
    "mcp": {
      "version": "1.0",
      "input_schema": {
        "type": "object",
        "properties": {
          "cParams": {
            "type": "string",
            "description": "ZSTD_compressionParameters"
          }
        },
        "required": [
          "cParams"
        ]
      },
      "execution": {
        "method": "dll_import",
        "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
        "function_name": "ZSTD_estimateCCtxSize_usingCParams",
        "calling_convention": "stdcall",
        "charset": "ansi"
      }
    },
    "metadata": {
      "is_signed": false,
      "publisher": null
    },
    "execution": {
      "method": "dll_import",
      "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
      "function_name": "ZSTD_estimateCCtxSize_usingCParams",
      "calling_convention": "stdcall",
      "charset": "ansi"
    },
    "return_type": "size_t",
    "parameters": [
      {
        "name": "cParams",
        "type": "ZSTD_compressionParameters",
        "required": true,
        "description": "ZSTD_compressionParameters"
      }
    ]
  },
  {
    "name": "ZSTD_estimateCDictSize",
    "tool_id": "dll_ZSTD_estimateCDictSize_112",
    "kind": "export",
    "ordinal": 112,
    "rva": "000129C0",
    "confidence": "guaranteed",
    "confidence_factors": {
      "has_signature": true,
      "has_documentation": true,
      "has_parameters": true,
      "has_return_type": true,
      "is_forwarded": true,
      "is_ordinal_only": false
    },
    "signature": {
      "return_type": "size_t",
      "parameters": "size_t dictSize, int compressionLevel",
      "calling_convention": null,
      "full_prototype": "ZSTDLIB_STATIC_API size_t ZSTD_estimateCDictSize(size_t dictSize, int compressionLevel)"
    },
    "documentation": {
      "summary": "! ZSTD_estimate?DictSize() :\n ZSTD_estimateCDictSize() will bet that src size is relatively \"small\", and content is copied, like ZSTD_createCDict().\n ZSTD_estimateCDictSize_advanced() makes it possible to control compression parameters precisely, like ZSTD_createCDict_advanced().\n Note : dictionaries created by reference (`ZSTD_dlm_byRef`) are logically smaller.",
      "description": "! ZSTD_estimate?DictSize() :\n ZSTD_estimateCDictSize() will bet that src size is relatively \"small\", and content is copied, like ZSTD_createCDict().\n ZSTD_estimateCDictSize_advanced() makes it possible to control compression parameters precisely, like ZSTD_createCDict_advanced().\n Note : dictionaries created by reference (`ZSTD_dlm_byRef`) are logically smaller.",
      "source_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "source_line": 1818
    },
    "evidence": {
      "discovered_by": "export_analyzer",
      "header_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "forwarded_to": null,
      "demangled_name": null
    },
    "mcp": {
      "version": "1.0",
      "input_schema": {
        "type": "object",
        "properties": {
          "dictSize": {
            "type": "integer",
            "description": "size_t"
          },
          "compressionLevel": {
            "type": "integer",
            "description": "int"
          }
        },
        "required": [
          "dictSize",
          "compressionLevel"
        ]
      },
      "execution": {
        "method": "dll_import",
        "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
        "function_name": "ZSTD_estimateCDictSize",
        "calling_convention": "stdcall",
        "charset": "ansi"
      }
    },
    "metadata": {
      "is_signed": false,
      "publisher": null
    },
    "execution": {
      "method": "dll_import",
      "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
      "function_name": "ZSTD_estimateCDictSize",
      "calling_convention": "stdcall",
      "charset": "ansi"
    },
    "return_type": "size_t",
    "parameters": [
      {
        "name": "dictSize",
        "type": "size_t",
        "required": true,
        "description": "size_t"
      },
      {
        "name": "compressionLevel",
        "type": "int",
        "required": true,
        "description": "int"
      }
    ]
  },
  {
    "name": "ZSTD_estimateCDictSize_advanced",
    "tool_id": "dll_ZSTD_estimateCDictSize_advanced_113",
    "kind": "export",
    "ordinal": 113,
    "rva": "00012C80",
    "confidence": "guaranteed",
    "confidence_factors": {
      "has_signature": true,
      "has_documentation": false,
      "has_parameters": true,
      "has_return_type": true,
      "is_forwarded": true,
      "is_ordinal_only": false
    },
    "signature": {
      "return_type": "size_t",
      "parameters": "size_t dictSize, ZSTD_compressionParameters cParams, ZSTD_dictLoadMethod_e dictLoadMethod",
      "calling_convention": null,
      "full_prototype": "ZSTDLIB_STATIC_API size_t ZSTD_estimateCDictSize_advanced(size_t dictSize, ZSTD_compressionParameters cParams, ZSTD_dictLoadMethod_e dictLoadMethod)"
    },
    "documentation": {
      "summary": "",
      "description": "",
      "source_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "source_line": 1819
    },
    "evidence": {
      "discovered_by": "export_analyzer",
      "header_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "forwarded_to": null,
      "demangled_name": null
    },
    "mcp": {
      "version": "1.0",
      "input_schema": {
        "type": "object",
        "properties": {
          "dictSize": {
            "type": "integer",
            "description": "size_t"
          },
          "cParams": {
            "type": "string",
            "description": "ZSTD_compressionParameters"
          },
          "dictLoadMethod": {
            "type": "string",
            "description": "ZSTD_dictLoadMethod_e"
          }
        },
        "required": [
          "dictSize",
          "cParams",
          "dictLoadMethod"
        ]
      },
      "execution": {
        "method": "dll_import",
        "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
        "function_name": "ZSTD_estimateCDictSize_advanced",
        "calling_convention": "stdcall",
        "charset": "ansi"
      }
    },
    "metadata": {
      "is_signed": false,
      "publisher": null
    },
    "execution": {
      "method": "dll_import",
      "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
      "function_name": "ZSTD_estimateCDictSize_advanced",
      "calling_convention": "stdcall",
      "charset": "ansi"
    },
    "return_type": "size_t",
    "parameters": [
      {
        "name": "dictSize",
        "type": "size_t",
        "required": true,
        "description": "size_t"
      },
      {
        "name": "cParams",
        "type": "ZSTD_compressionParameters",
        "required": true,
        "description": "ZSTD_compressionParameters"
      },
      {
        "name": "dictLoadMethod",
        "type": "ZSTD_dictLoadMethod_e",
        "required": true,
        "description": "ZSTD_dictLoadMethod_e"
      }
    ]
  },
  {
    "name": "ZSTD_estimateCStreamSize",
    "tool_id": "dll_ZSTD_estimateCStreamSize_114",
    "kind": "export",
    "ordinal": 114,
    "rva": "00012D20",
    "confidence": "guaranteed",
    "confidence_factors": {
      "has_signature": true,
      "has_documentation": true,
      "has_parameters": true,
      "has_return_type": true,
      "is_forwarded": true,
      "is_ordinal_only": false
    },
    "signature": {
      "return_type": "size_t",
      "parameters": "int maxCompressionLevel",
      "calling_convention": null,
      "full_prototype": "ZSTDLIB_STATIC_API size_t ZSTD_estimateCStreamSize(int maxCompressionLevel)"
    },
    "documentation": {
      "summary": "! ZSTD_estimateCStreamSize() :\n ZSTD_estimateCStreamSize() will provide a memory budget large enough for streaming compression\n using any compression level up to the max specified one.\n It will also consider src size to be arbitrarily \"large\", which is a worst case scenario.\n If srcSize is known to always be small, ZSTD_estimateCStreamSize_usingCParams() can provide a tighter estimation.\n ZSTD_estimateCStreamSize_usingCParams() can be used in tandem with ZSTD_getCParams() to create cParams from compressionLevel.\n ZSTD_estimateCStreamSize_usingCCtxParams() can be used in tandem with ZSTD_CCtxParams_setParameter(). Only single-threaded compression is supported. This function will return an error code if ZSTD_c_nbWorkers is >= 1.\n Note : CStream size estimation is only correct for single-threaded compression.\n ZSTD_estimateCStreamSize_usingCCtxParams() will return an error code if ZSTD_c_nbWorkers is >= 1.\n Note 2 : ZSTD_estimateCStreamSize* functions are not compatible with the Block-Level Sequence Producer API at this time.\n Size estimates assume that no external sequence producer is registered.\n\n ZSTD_DStream memory budget depends on frame's window Size.\n This information can be passed manually, using ZSTD_estimateDStreamSize,\n or deducted from a valid frame Header, using ZSTD_estimateDStreamSize_fromFrame();\n Any frame requesting a window size larger than max specified one will be rejected.\n Note : if streaming is init with function ZSTD_init?Stream_usingDict(),\n        an internal ?Dict will be created, which additional size is not estimated here.\n        In this case, get total size by adding ZSTD_estimate?DictSize",
      "description": "! ZSTD_estimateCStreamSize() :\n ZSTD_estimateCStreamSize() will provide a memory budget large enough for streaming compression\n using any compression level up to the max specified one.\n It will also consider src size to be arbitrarily \"large\", which is a worst case scenario.\n If srcSize is known to always be small, ZSTD_estimateCStreamSize_usingCParams() can provide a tighter estimation.\n ZSTD_estimateCStreamSize_usingCParams() can be used in tandem with ZSTD_getCParams() to create cParams from compressionLevel.\n ZSTD_estimateCStreamSize_usingCCtxParams() can be used in tandem with ZSTD_CCtxParams_setParameter(). Only single-threaded compression is supported. This function will return an error code if ZSTD_c_nbWorkers is >= 1.\n Note : CStream size estimation is only correct for single-threaded compression.\n ZSTD_estimateCStreamSize_usingCCtxParams() will return an error code if ZSTD_c_nbWorkers is >= 1.\n Note 2 : ZSTD_estimateCStreamSize* functions are not compatible with the Block-Level Sequence Producer API at this time.\n Size estimates assume that no external sequence producer is registered.\n\n ZSTD_DStream memory budget depends on frame's window Size.\n This information can be passed manually, using ZSTD_estimateDStreamSize,\n or deducted from a valid frame Header, using ZSTD_estimateDStreamSize_fromFrame();\n Any frame requesting a window size larger than max specified one will be rejected.\n Note : if streaming is init with function ZSTD_init?Stream_usingDict(),\n        an internal ?Dict will be created, which additional size is not estimated here.\n        In this case, get total size by adding ZSTD_estimate?DictSize",
      "source_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "source_line": 1807
    },
    "evidence": {
      "discovered_by": "export_analyzer",
      "header_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "forwarded_to": null,
      "demangled_name": null
    },
    "mcp": {
      "version": "1.0",
      "input_schema": {
        "type": "object",
        "properties": {
          "maxCompressionLevel": {
            "type": "integer",
            "description": "int"
          }
        },
        "required": [
          "maxCompressionLevel"
        ]
      },
      "execution": {
        "method": "dll_import",
        "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
        "function_name": "ZSTD_estimateCStreamSize",
        "calling_convention": "stdcall",
        "charset": "ansi"
      }
    },
    "metadata": {
      "is_signed": false,
      "publisher": null
    },
    "execution": {
      "method": "dll_import",
      "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
      "function_name": "ZSTD_estimateCStreamSize",
      "calling_convention": "stdcall",
      "charset": "ansi"
    },
    "return_type": "size_t",
    "parameters": [
      {
        "name": "maxCompressionLevel",
        "type": "int",
        "required": true,
        "description": "int"
      }
    ]
  },
  {
    "name": "ZSTD_estimateCStreamSize_usingCCtxParams",
    "tool_id": "dll_ZSTD_estimateCStreamSize_usingCCtxParams_115",
    "kind": "export",
    "ordinal": 115,
    "rva": "00012EB0",
    "confidence": "guaranteed",
    "confidence_factors": {
      "has_signature": true,
      "has_documentation": false,
      "has_parameters": true,
      "has_return_type": true,
      "is_forwarded": true,
      "is_ordinal_only": false
    },
    "signature": {
      "return_type": "size_t",
      "parameters": "const ZSTD_CCtx_params* params",
      "calling_convention": null,
      "full_prototype": "ZSTDLIB_STATIC_API size_t ZSTD_estimateCStreamSize_usingCCtxParams(const ZSTD_CCtx_params* params)"
    },
    "documentation": {
      "summary": "",
      "description": "",
      "source_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "source_line": 1809
    },
    "evidence": {
      "discovered_by": "export_analyzer",
      "header_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "forwarded_to": null,
      "demangled_name": null
    },
    "mcp": {
      "version": "1.0",
      "input_schema": {
        "type": "object",
        "properties": {
          "params": {
            "type": "string",
            "description": "ZSTD_CCtx_params*"
          }
        },
        "required": [
          "params"
        ]
      },
      "execution": {
        "method": "dll_import",
        "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
        "function_name": "ZSTD_estimateCStreamSize_usingCCtxParams",
        "calling_convention": "stdcall",
        "charset": "ansi"
      }
    },
    "metadata": {
      "is_signed": false,
      "publisher": null
    },
    "execution": {
      "method": "dll_import",
      "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
      "function_name": "ZSTD_estimateCStreamSize_usingCCtxParams",
      "calling_convention": "stdcall",
      "charset": "ansi"
    },
    "return_type": "size_t",
    "parameters": [
      {
        "name": "params",
        "type": "const ZSTD_CCtx_params*",
        "required": true,
        "description": "const ZSTD_CCtx_params*"
      }
    ]
  },
  {
    "name": "ZSTD_estimateCStreamSize_usingCParams",
    "tool_id": "dll_ZSTD_estimateCStreamSize_usingCParams_116",
    "kind": "export",
    "ordinal": 116,
    "rva": "00013020",
    "confidence": "guaranteed",
    "confidence_factors": {
      "has_signature": true,
      "has_documentation": false,
      "has_parameters": true,
      "has_return_type": true,
      "is_forwarded": true,
      "is_ordinal_only": false
    },
    "signature": {
      "return_type": "size_t",
      "parameters": "ZSTD_compressionParameters cParams",
      "calling_convention": null,
      "full_prototype": "ZSTDLIB_STATIC_API size_t ZSTD_estimateCStreamSize_usingCParams(ZSTD_compressionParameters cParams)"
    },
    "documentation": {
      "summary": "",
      "description": "",
      "source_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "source_line": 1808
    },
    "evidence": {
      "discovered_by": "export_analyzer",
      "header_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "forwarded_to": null,
      "demangled_name": null
    },
    "mcp": {
      "version": "1.0",
      "input_schema": {
        "type": "object",
        "properties": {
          "cParams": {
            "type": "string",
            "description": "ZSTD_compressionParameters"
          }
        },
        "required": [
          "cParams"
        ]
      },
      "execution": {
        "method": "dll_import",
        "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
        "function_name": "ZSTD_estimateCStreamSize_usingCParams",
        "calling_convention": "stdcall",
        "charset": "ansi"
      }
    },
    "metadata": {
      "is_signed": false,
      "publisher": null
    },
    "execution": {
      "method": "dll_import",
      "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
      "function_name": "ZSTD_estimateCStreamSize_usingCParams",
      "calling_convention": "stdcall",
      "charset": "ansi"
    },
    "return_type": "size_t",
    "parameters": [
      {
        "name": "cParams",
        "type": "ZSTD_compressionParameters",
        "required": true,
        "description": "ZSTD_compressionParameters"
      }
    ]
  },
  {
    "name": "ZSTD_estimateDCtxSize",
    "tool_id": "dll_ZSTD_estimateDCtxSize_117",
    "kind": "export",
    "ordinal": 117,
    "rva": "00067E70",
    "confidence": "guaranteed",
    "confidence_factors": {
      "has_signature": true,
      "has_documentation": false,
      "has_parameters": true,
      "has_return_type": true,
      "is_forwarded": true,
      "is_ordinal_only": false
    },
    "signature": {
      "return_type": "size_t",
      "parameters": "void",
      "calling_convention": null,
      "full_prototype": "ZSTDLIB_STATIC_API size_t ZSTD_estimateDCtxSize(void)"
    },
    "documentation": {
      "summary": "",
      "description": "",
      "source_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "source_line": 1785
    },
    "evidence": {
      "discovered_by": "export_analyzer",
      "header_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "forwarded_to": null,
      "demangled_name": null
    },
    "mcp": {
      "version": "1.0",
      "input_schema": {
        "type": "object",
        "properties": {},
        "required": []
      },
      "execution": {
        "method": "dll_import",
        "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
        "function_name": "ZSTD_estimateDCtxSize",
        "calling_convention": "stdcall",
        "charset": "ansi"
      }
    },
    "metadata": {
      "is_signed": false,
      "publisher": null
    },
    "execution": {
      "method": "dll_import",
      "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
      "function_name": "ZSTD_estimateDCtxSize",
      "calling_convention": "stdcall",
      "charset": "ansi"
    },
    "return_type": "size_t"
  },
  {
    "name": "ZSTD_estimateDDictSize",
    "tool_id": "dll_ZSTD_estimateDDictSize_118",
    "kind": "export",
    "ordinal": 118,
    "rva": "000647D0",
    "confidence": "guaranteed",
    "confidence_factors": {
      "has_signature": true,
      "has_documentation": false,
      "has_parameters": true,
      "has_return_type": true,
      "is_forwarded": true,
      "is_ordinal_only": false
    },
    "signature": {
      "return_type": "size_t",
      "parameters": "size_t dictSize, ZSTD_dictLoadMethod_e dictLoadMethod",
      "calling_convention": null,
      "full_prototype": "ZSTDLIB_STATIC_API size_t ZSTD_estimateDDictSize(size_t dictSize, ZSTD_dictLoadMethod_e dictLoadMethod)"
    },
    "documentation": {
      "summary": "",
      "description": "",
      "source_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "source_line": 1820
    },
    "evidence": {
      "discovered_by": "export_analyzer",
      "header_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "forwarded_to": null,
      "demangled_name": null
    },
    "mcp": {
      "version": "1.0",
      "input_schema": {
        "type": "object",
        "properties": {
          "dictSize": {
            "type": "integer",
            "description": "size_t"
          },
          "dictLoadMethod": {
            "type": "string",
            "description": "ZSTD_dictLoadMethod_e"
          }
        },
        "required": [
          "dictSize",
          "dictLoadMethod"
        ]
      },
      "execution": {
        "method": "dll_import",
        "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
        "function_name": "ZSTD_estimateDDictSize",
        "calling_convention": "stdcall",
        "charset": "ansi"
      }
    },
    "metadata": {
      "is_signed": false,
      "publisher": null
    },
    "execution": {
      "method": "dll_import",
      "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
      "function_name": "ZSTD_estimateDDictSize",
      "calling_convention": "stdcall",
      "charset": "ansi"
    },
    "return_type": "size_t",
    "parameters": [
      {
        "name": "dictSize",
        "type": "size_t",
        "required": true,
        "description": "size_t"
      },
      {
        "name": "dictLoadMethod",
        "type": "ZSTD_dictLoadMethod_e",
        "required": true,
        "description": "ZSTD_dictLoadMethod_e"
      }
    ]
  },
  {
    "name": "ZSTD_estimateDStreamSize",
    "tool_id": "dll_ZSTD_estimateDStreamSize_119",
    "kind": "export",
    "ordinal": 119,
    "rva": "00067E80",
    "confidence": "guaranteed",
    "confidence_factors": {
      "has_signature": true,
      "has_documentation": false,
      "has_parameters": true,
      "has_return_type": true,
      "is_forwarded": true,
      "is_ordinal_only": false
    },
    "signature": {
      "return_type": "size_t",
      "parameters": "size_t maxWindowSize",
      "calling_convention": null,
      "full_prototype": "ZSTDLIB_STATIC_API size_t ZSTD_estimateDStreamSize(size_t maxWindowSize)"
    },
    "documentation": {
      "summary": "",
      "description": "",
      "source_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "source_line": 1810
    },
    "evidence": {
      "discovered_by": "export_analyzer",
      "header_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "forwarded_to": null,
      "demangled_name": null
    },
    "mcp": {
      "version": "1.0",
      "input_schema": {
        "type": "object",
        "properties": {
          "maxWindowSize": {
            "type": "integer",
            "description": "size_t"
          }
        },
        "required": [
          "maxWindowSize"
        ]
      },
      "execution": {
        "method": "dll_import",
        "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
        "function_name": "ZSTD_estimateDStreamSize",
        "calling_convention": "stdcall",
        "charset": "ansi"
      }
    },
    "metadata": {
      "is_signed": false,
      "publisher": null
    },
    "execution": {
      "method": "dll_import",
      "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
      "function_name": "ZSTD_estimateDStreamSize",
      "calling_convention": "stdcall",
      "charset": "ansi"
    },
    "return_type": "size_t",
    "parameters": [
      {
        "name": "maxWindowSize",
        "type": "size_t",
        "required": true,
        "description": "size_t"
      }
    ]
  },
  {
    "name": "ZSTD_estimateDStreamSize_fromFrame",
    "tool_id": "dll_ZSTD_estimateDStreamSize_fromFrame_120",
    "kind": "export",
    "ordinal": 120,
    "rva": "00067EB0",
    "confidence": "guaranteed",
    "confidence_factors": {
      "has_signature": true,
      "has_documentation": false,
      "has_parameters": true,
      "has_return_type": true,
      "is_forwarded": true,
      "is_ordinal_only": false
    },
    "signature": {
      "return_type": "size_t",
      "parameters": "const void* src, size_t srcSize",
      "calling_convention": null,
      "full_prototype": "ZSTDLIB_STATIC_API size_t ZSTD_estimateDStreamSize_fromFrame(const void* src, size_t srcSize)"
    },
    "documentation": {
      "summary": "",
      "description": "",
      "source_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "source_line": 1811
    },
    "evidence": {
      "discovered_by": "export_analyzer",
      "header_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "forwarded_to": null,
      "demangled_name": null
    },
    "mcp": {
      "version": "1.0",
      "input_schema": {
        "type": "object",
        "properties": {
          "src": {
            "type": "string",
            "description": "void*"
          },
          "srcSize": {
            "type": "integer",
            "description": "size_t"
          }
        },
        "required": [
          "src",
          "srcSize"
        ]
      },
      "execution": {
        "method": "dll_import",
        "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
        "function_name": "ZSTD_estimateDStreamSize_fromFrame",
        "calling_convention": "stdcall",
        "charset": "ansi"
      }
    },
    "metadata": {
      "is_signed": false,
      "publisher": null
    },
    "execution": {
      "method": "dll_import",
      "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
      "function_name": "ZSTD_estimateDStreamSize_fromFrame",
      "calling_convention": "stdcall",
      "charset": "ansi"
    },
    "return_type": "size_t",
    "parameters": [
      {
        "name": "src",
        "type": "const void*",
        "required": true,
        "description": "const void*"
      },
      {
        "name": "srcSize",
        "type": "size_t",
        "required": true,
        "description": "size_t"
      }
    ]
  },
  {
    "name": "ZSTD_findDecompressedSize",
    "tool_id": "dll_ZSTD_findDecompressedSize_121",
    "kind": "export",
    "ordinal": 121,
    "rva": "00067F20",
    "confidence": "guaranteed",
    "confidence_factors": {
      "has_signature": true,
      "has_documentation": true,
      "has_parameters": true,
      "has_return_type": true,
      "is_forwarded": true,
      "is_ordinal_only": false
    },
    "signature": {
      "return_type": "unsigned long long",
      "parameters": "const void* src, size_t srcSize",
      "calling_convention": null,
      "full_prototype": "ZSTDLIB_STATIC_API unsigned long long ZSTD_findDecompressedSize(const void* src, size_t srcSize)"
    },
    "documentation": {
      "summary": "! ZSTD_findDecompressedSize() :\n `src` should point to the start of a series of ZSTD encoded and/or skippable frames\n `srcSize` must be the _exact_ size of this series\n      (i.e. there should be a frame boundary at `src + srcSize`)\n @return : - decompressed size of all data in all successive frames\n           - if the decompressed size cannot be determined: ZSTD_CONTENTSIZE_UNKNOWN\n           - if an error occurred: ZSTD_CONTENTSIZE_ERROR\n\n  note 1 : decompressed size is an optional field, that may not be present, especially in streaming mode.\n           When `return==ZSTD_CONTENTSIZE_UNKNOWN`, data to decompress could be any size.\n           In which case, it's necessary to use streaming mode to decompress data.\n  note 2 : decompressed size is always present when compression is done with ZSTD_compress()\n  note 3 : decompressed size can be very large (64-bits value),\n           potentially larger than what local system can handle as a single memory segment.\n           In which case, it's necessary to use streaming mode to decompress data.\n  note 4 : If source is untrusted, decompressed size could be wrong or intentionally modified.\n           Always ensure result fits within application's authorized limits.\n           Each application can set its own limits.\n  note 5 : ZSTD_findDecompressedSize handles multiple frames, and so it must traverse the input to\n           read each contained frame header.  This is fast as most of the data is skipped,\n           however it does mean that all frame data must be present and valid.",
      "description": "! ZSTD_findDecompressedSize() :\n `src` should point to the start of a series of ZSTD encoded and/or skippable frames\n `srcSize` must be the _exact_ size of this series\n      (i.e. there should be a frame boundary at `src + srcSize`)\n @return : - decompressed size of all data in all successive frames\n           - if the decompressed size cannot be determined: ZSTD_CONTENTSIZE_UNKNOWN\n           - if an error occurred: ZSTD_CONTENTSIZE_ERROR\n\n  note 1 : decompressed size is an optional field, that may not be present, especially in streaming mode.\n           When `return==ZSTD_CONTENTSIZE_UNKNOWN`, data to decompress could be any size.\n           In which case, it's necessary to use streaming mode to decompress data.\n  note 2 : decompressed size is always present when compression is done with ZSTD_compress()\n  note 3 : decompressed size can be very large (64-bits value),\n           potentially larger than what local system can handle as a single memory segment.\n           In which case, it's necessary to use streaming mode to decompress data.\n  note 4 : If source is untrusted, decompressed size could be wrong or intentionally modified.\n           Always ensure result fits within application's authorized limits.\n           Each application can set its own limits.\n  note 5 : ZSTD_findDecompressedSize handles multiple frames, and so it must traverse the input to\n           read each contained frame header.  This is fast as most of the data is skipped,\n           however it does mean that all frame data must be present and valid.",
      "source_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "source_line": 1487
    },
    "evidence": {
      "discovered_by": "export_analyzer",
      "header_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "forwarded_to": null,
      "demangled_name": null
    },
    "mcp": {
      "version": "1.0",
      "input_schema": {
        "type": "object",
        "properties": {
          "src": {
            "type": "string",
            "description": "void*"
          },
          "srcSize": {
            "type": "integer",
            "description": "size_t"
          }
        },
        "required": [
          "src",
          "srcSize"
        ]
      },
      "execution": {
        "method": "dll_import",
        "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
        "function_name": "ZSTD_findDecompressedSize",
        "calling_convention": "stdcall",
        "charset": "ansi"
      }
    },
    "metadata": {
      "is_signed": false,
      "publisher": null
    },
    "execution": {
      "method": "dll_import",
      "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
      "function_name": "ZSTD_findDecompressedSize",
      "calling_convention": "stdcall",
      "charset": "ansi"
    },
    "return_type": "unsigned long long",
    "parameters": [
      {
        "name": "src",
        "type": "const void*",
        "required": true,
        "description": "const void*"
      },
      {
        "name": "srcSize",
        "type": "size_t",
        "required": true,
        "description": "size_t"
      }
    ]
  },
  {
    "name": "ZSTD_findFrameCompressedSize",
    "tool_id": "dll_ZSTD_findFrameCompressedSize_122",
    "kind": "export",
    "ordinal": 122,
    "rva": "00067FF0",
    "confidence": "guaranteed",
    "confidence_factors": {
      "has_signature": true,
      "has_documentation": true,
      "has_parameters": true,
      "has_return_type": true,
      "is_forwarded": true,
      "is_ordinal_only": false
    },
    "signature": {
      "return_type": "size_t",
      "parameters": "const void* src, size_t srcSize",
      "calling_convention": null,
      "full_prototype": "ZSTDLIB_API size_t ZSTD_findFrameCompressedSize(const void* src, size_t srcSize)"
    },
    "documentation": {
      "summary": "! ZSTD_findFrameCompressedSize() : Requires v1.4.0+\n`src` should point to the start of a ZSTD frame or skippable frame.\n`srcSize` must be >= first frame size\n@return : the compressed size of the first frame starting at `src`,\n          suitable to pass as `srcSize` to `ZSTD_decompress` or similar,\n          or an error code if input is invalid\n Note 1: this method is called _find*() because it's not enough to read the header,\n         it may have to scan through the frame's content, to reach its end.\n Note 2: this method also works with Skippable Frames. In which case,\n         it returns the size of the complete skippable frame,\n         which is always equal to its content size + 8 bytes for headers.",
      "description": "! ZSTD_findFrameCompressedSize() : Requires v1.4.0+\n`src` should point to the start of a ZSTD frame or skippable frame.\n`srcSize` must be >= first frame size\n@return : the compressed size of the first frame starting at `src`,\n          suitable to pass as `srcSize` to `ZSTD_decompress` or similar,\n          or an error code if input is invalid\n Note 1: this method is called _find*() because it's not enough to read the header,\n         it may have to scan through the frame's content, to reach its end.\n Note 2: this method also works with Skippable Frames. In which case,\n         it returns the size of the complete skippable frame,\n         which is always equal to its content size + 8 bytes for headers.",
      "source_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "source_line": 227
    },
    "evidence": {
      "discovered_by": "export_analyzer",
      "header_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "forwarded_to": null,
      "demangled_name": null
    },
    "mcp": {
      "version": "1.0",
      "input_schema": {
        "type": "object",
        "properties": {
          "src": {
            "type": "string",
            "description": "void*"
          },
          "srcSize": {
            "type": "integer",
            "description": "size_t"
          }
        },
        "required": [
          "src",
          "srcSize"
        ]
      },
      "execution": {
        "method": "dll_import",
        "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
        "function_name": "ZSTD_findFrameCompressedSize",
        "calling_convention": "stdcall",
        "charset": "ansi"
      }
    },
    "metadata": {
      "is_signed": false,
      "publisher": null
    },
    "execution": {
      "method": "dll_import",
      "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
      "function_name": "ZSTD_findFrameCompressedSize",
      "calling_convention": "stdcall",
      "charset": "ansi"
    },
    "return_type": "size_t",
    "parameters": [
      {
        "name": "src",
        "type": "const void*",
        "required": true,
        "description": "const void*"
      },
      {
        "name": "srcSize",
        "type": "size_t",
        "required": true,
        "description": "size_t"
      }
    ]
  },
  {
    "name": "ZSTD_flushStream",
    "tool_id": "dll_ZSTD_flushStream_123",
    "kind": "export",
    "ordinal": 123,
    "rva": "00013150",
    "confidence": "guaranteed",
    "confidence_factors": {
      "has_signature": true,
      "has_documentation": true,
      "has_parameters": true,
      "has_return_type": true,
      "is_forwarded": true,
      "is_ordinal_only": false
    },
    "signature": {
      "return_type": "size_t",
      "parameters": "ZSTD_CStream* zcs, ZSTD_outBuffer* output",
      "calling_convention": null,
      "full_prototype": "ZSTDLIB_API size_t ZSTD_flushStream(ZSTD_CStream* zcs, ZSTD_outBuffer* output)"
    },
    "documentation": {
      "summary": "! Equivalent to ZSTD_compressStream2(zcs, output, &emptyInput, ZSTD_e_flush).",
      "description": "! Equivalent to ZSTD_compressStream2(zcs, output, &emptyInput, ZSTD_e_flush).",
      "source_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "source_line": 871
    },
    "evidence": {
      "discovered_by": "export_analyzer",
      "header_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "forwarded_to": null,
      "demangled_name": null
    },
    "mcp": {
      "version": "1.0",
      "input_schema": {
        "type": "object",
        "properties": {
          "zcs": {
            "type": "string",
            "description": "ZSTD_CStream*"
          },
          "output": {
            "type": "string",
            "description": "ZSTD_outBuffer*"
          }
        },
        "required": [
          "zcs",
          "output"
        ]
      },
      "execution": {
        "method": "dll_import",
        "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
        "function_name": "ZSTD_flushStream",
        "calling_convention": "stdcall",
        "charset": "ansi"
      }
    },
    "metadata": {
      "is_signed": false,
      "publisher": null
    },
    "execution": {
      "method": "dll_import",
      "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
      "function_name": "ZSTD_flushStream",
      "calling_convention": "stdcall",
      "charset": "ansi"
    },
    "return_type": "size_t",
    "parameters": [
      {
        "name": "zcs",
        "type": "ZSTD_CStream*",
        "required": true,
        "description": "ZSTD_CStream*"
      },
      {
        "name": "output",
        "type": "ZSTD_outBuffer*",
        "required": true,
        "description": "ZSTD_outBuffer*"
      }
    ]
  },
  {
    "name": "ZSTD_frameHeaderSize",
    "tool_id": "dll_ZSTD_frameHeaderSize_124",
    "kind": "export",
    "ordinal": 124,
    "rva": "00068330",
    "confidence": "guaranteed",
    "confidence_factors": {
      "has_signature": true,
      "has_documentation": true,
      "has_parameters": true,
      "has_return_type": true,
      "is_forwarded": true,
      "is_ordinal_only": false
    },
    "signature": {
      "return_type": "size_t",
      "parameters": "const void* src, size_t srcSize",
      "calling_convention": null,
      "full_prototype": "ZSTDLIB_STATIC_API size_t ZSTD_frameHeaderSize(const void* src, size_t srcSize)"
    },
    "documentation": {
      "summary": "! ZSTD_frameHeaderSize() :\n srcSize must be large enough, aka >= ZSTD_FRAMEHEADERSIZE_PREFIX.\n@return : size of the Frame Header,\n          or an error code (if srcSize is too small)",
      "description": "! ZSTD_frameHeaderSize() :\n srcSize must be large enough, aka >= ZSTD_FRAMEHEADERSIZE_PREFIX.\n@return : size of the Frame Header,\n          or an error code (if srcSize is too small)",
      "source_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "source_line": 1508
    },
    "evidence": {
      "discovered_by": "export_analyzer",
      "header_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "forwarded_to": null,
      "demangled_name": null
    },
    "mcp": {
      "version": "1.0",
      "input_schema": {
        "type": "object",
        "properties": {
          "src": {
            "type": "string",
            "description": "void*"
          },
          "srcSize": {
            "type": "integer",
            "description": "size_t"
          }
        },
        "required": [
          "src",
          "srcSize"
        ]
      },
      "execution": {
        "method": "dll_import",
        "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
        "function_name": "ZSTD_frameHeaderSize",
        "calling_convention": "stdcall",
        "charset": "ansi"
      }
    },
    "metadata": {
      "is_signed": false,
      "publisher": null
    },
    "execution": {
      "method": "dll_import",
      "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
      "function_name": "ZSTD_frameHeaderSize",
      "calling_convention": "stdcall",
      "charset": "ansi"
    },
    "return_type": "size_t",
    "parameters": [
      {
        "name": "src",
        "type": "const void*",
        "required": true,
        "description": "const void*"
      },
      {
        "name": "srcSize",
        "type": "size_t",
        "required": true,
        "description": "size_t"
      }
    ]
  },
  {
    "name": "ZSTD_freeCCtx",
    "tool_id": "dll_ZSTD_freeCCtx_125",
    "kind": "export",
    "ordinal": 125,
    "rva": "000131B0",
    "confidence": "guaranteed",
    "confidence_factors": {
      "has_signature": true,
      "has_documentation": true,
      "has_parameters": true,
      "has_return_type": true,
      "is_forwarded": true,
      "is_ordinal_only": false
    },
    "signature": {
      "return_type": "size_t",
      "parameters": "ZSTD_CCtx* cctx",
      "calling_convention": null,
      "full_prototype": "ZSTDLIB_API size_t ZSTD_freeCCtx(ZSTD_CCtx* cctx)"
    },
    "documentation": {
      "summary": "compatible with NULL pointer",
      "description": "compatible with NULL pointer",
      "source_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "source_line": 282
    },
    "evidence": {
      "discovered_by": "export_analyzer",
      "header_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "forwarded_to": null,
      "demangled_name": null
    },
    "mcp": {
      "version": "1.0",
      "input_schema": {
        "type": "object",
        "properties": {
          "cctx": {
            "type": "string",
            "description": "ZSTD_CCtx*"
          }
        },
        "required": [
          "cctx"
        ]
      },
      "execution": {
        "method": "dll_import",
        "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
        "function_name": "ZSTD_freeCCtx",
        "calling_convention": "stdcall",
        "charset": "ansi"
      }
    },
    "metadata": {
      "is_signed": false,
      "publisher": null
    },
    "execution": {
      "method": "dll_import",
      "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
      "function_name": "ZSTD_freeCCtx",
      "calling_convention": "stdcall",
      "charset": "ansi"
    },
    "return_type": "size_t",
    "parameters": [
      {
        "name": "cctx",
        "type": "ZSTD_CCtx*",
        "required": true,
        "description": "ZSTD_CCtx*"
      }
    ]
  },
  {
    "name": "ZSTD_freeCCtxParams",
    "tool_id": "dll_ZSTD_freeCCtxParams_126",
    "kind": "export",
    "ordinal": 126,
    "rva": "000132E0",
    "confidence": "guaranteed",
    "confidence_factors": {
      "has_signature": true,
      "has_documentation": true,
      "has_parameters": true,
      "has_return_type": true,
      "is_forwarded": true,
      "is_ordinal_only": false
    },
    "signature": {
      "return_type": "size_t",
      "parameters": "ZSTD_CCtx_params* params",
      "calling_convention": null,
      "full_prototype": "ZSTDLIB_STATIC_API size_t ZSTD_freeCCtxParams(ZSTD_CCtx_params* params)"
    },
    "documentation": {
      "summary": "accept NULL pointer",
      "description": "accept NULL pointer",
      "source_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "source_line": 2385
    },
    "evidence": {
      "discovered_by": "export_analyzer",
      "header_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "forwarded_to": null,
      "demangled_name": null
    },
    "mcp": {
      "version": "1.0",
      "input_schema": {
        "type": "object",
        "properties": {
          "params": {
            "type": "string",
            "description": "ZSTD_CCtx_params*"
          }
        },
        "required": [
          "params"
        ]
      },
      "execution": {
        "method": "dll_import",
        "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
        "function_name": "ZSTD_freeCCtxParams",
        "calling_convention": "stdcall",
        "charset": "ansi"
      }
    },
    "metadata": {
      "is_signed": false,
      "publisher": null
    },
    "execution": {
      "method": "dll_import",
      "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
      "function_name": "ZSTD_freeCCtxParams",
      "calling_convention": "stdcall",
      "charset": "ansi"
    },
    "return_type": "size_t",
    "parameters": [
      {
        "name": "params",
        "type": "ZSTD_CCtx_params*",
        "required": true,
        "description": "ZSTD_CCtx_params*"
      }
    ]
  },
  {
    "name": "ZSTD_freeCDict",
    "tool_id": "dll_ZSTD_freeCDict_127",
    "kind": "export",
    "ordinal": 127,
    "rva": "00013330",
    "confidence": "guaranteed",
    "confidence_factors": {
      "has_signature": true,
      "has_documentation": true,
      "has_parameters": true,
      "has_return_type": true,
      "is_forwarded": true,
      "is_ordinal_only": false
    },
    "signature": {
      "return_type": "size_t",
      "parameters": "ZSTD_CDict* CDict",
      "calling_convention": null,
      "full_prototype": "ZSTDLIB_API size_t ZSTD_freeCDict(ZSTD_CDict* CDict)"
    },
    "documentation": {
      "summary": "! ZSTD_freeCDict() :\n Function frees memory allocated by ZSTD_createCDict().\n If a NULL pointer is passed, no operation is performed.",
      "description": "! ZSTD_freeCDict() :\n Function frees memory allocated by ZSTD_createCDict().\n If a NULL pointer is passed, no operation is performed.",
      "source_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "source_line": 1005
    },
    "evidence": {
      "discovered_by": "export_analyzer",
      "header_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "forwarded_to": null,
      "demangled_name": null
    },
    "mcp": {
      "version": "1.0",
      "input_schema": {
        "type": "object",
        "properties": {
          "CDict": {
            "type": "string",
            "description": "ZSTD_CDict*"
          }
        },
        "required": [
          "CDict"
        ]
      },
      "execution": {
        "method": "dll_import",
        "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
        "function_name": "ZSTD_freeCDict",
        "calling_convention": "stdcall",
        "charset": "ansi"
      }
    },
    "metadata": {
      "is_signed": false,
      "publisher": null
    },
    "execution": {
      "method": "dll_import",
      "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
      "function_name": "ZSTD_freeCDict",
      "calling_convention": "stdcall",
      "charset": "ansi"
    },
    "return_type": "size_t",
    "parameters": [
      {
        "name": "CDict",
        "type": "ZSTD_CDict*",
        "required": true,
        "description": "ZSTD_CDict*"
      }
    ]
  },
  {
    "name": "ZSTD_freeCStream",
    "tool_id": "dll_ZSTD_freeCStream_128",
    "kind": "export",
    "ordinal": 128,
    "rva": "000131B0",
    "confidence": "guaranteed",
    "confidence_factors": {
      "has_signature": true,
      "has_documentation": true,
      "has_parameters": true,
      "has_return_type": true,
      "is_forwarded": true,
      "is_ordinal_only": false
    },
    "signature": {
      "return_type": "size_t",
      "parameters": "ZSTD_CStream* zcs",
      "calling_convention": null,
      "full_prototype": "ZSTDLIB_API size_t ZSTD_freeCStream(ZSTD_CStream* zcs)"
    },
    "documentation": {
      "summary": "accept NULL pointer",
      "description": "accept NULL pointer",
      "source_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "source_line": 780
    },
    "evidence": {
      "discovered_by": "export_analyzer",
      "header_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "forwarded_to": null,
      "demangled_name": null
    },
    "mcp": {
      "version": "1.0",
      "input_schema": {
        "type": "object",
        "properties": {
          "zcs": {
            "type": "string",
            "description": "ZSTD_CStream*"
          }
        },
        "required": [
          "zcs"
        ]
      },
      "execution": {
        "method": "dll_import",
        "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
        "function_name": "ZSTD_freeCStream",
        "calling_convention": "stdcall",
        "charset": "ansi"
      }
    },
    "metadata": {
      "is_signed": false,
      "publisher": null
    },
    "execution": {
      "method": "dll_import",
      "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
      "function_name": "ZSTD_freeCStream",
      "calling_convention": "stdcall",
      "charset": "ansi"
    },
    "return_type": "size_t",
    "parameters": [
      {
        "name": "zcs",
        "type": "ZSTD_CStream*",
        "required": true,
        "description": "ZSTD_CStream*"
      }
    ]
  },
  {
    "name": "ZSTD_freeDCtx",
    "tool_id": "dll_ZSTD_freeDCtx_129",
    "kind": "export",
    "ordinal": 129,
    "rva": "00068400",
    "confidence": "guaranteed",
    "confidence_factors": {
      "has_signature": true,
      "has_documentation": true,
      "has_parameters": true,
      "has_return_type": true,
      "is_forwarded": true,
      "is_ordinal_only": false
    },
    "signature": {
      "return_type": "size_t",
      "parameters": "ZSTD_DCtx* dctx",
      "calling_convention": null,
      "full_prototype": "ZSTDLIB_API size_t ZSTD_freeDCtx(ZSTD_DCtx* dctx)"
    },
    "documentation": {
      "summary": "accept NULL pointer",
      "description": "accept NULL pointer",
      "source_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "source_line": 305
    },
    "evidence": {
      "discovered_by": "export_analyzer",
      "header_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "forwarded_to": null,
      "demangled_name": null
    },
    "mcp": {
      "version": "1.0",
      "input_schema": {
        "type": "object",
        "properties": {
          "dctx": {
            "type": "string",
            "description": "ZSTD_DCtx*"
          }
        },
        "required": [
          "dctx"
        ]
      },
      "execution": {
        "method": "dll_import",
        "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
        "function_name": "ZSTD_freeDCtx",
        "calling_convention": "stdcall",
        "charset": "ansi"
      }
    },
    "metadata": {
      "is_signed": false,
      "publisher": null
    },
    "execution": {
      "method": "dll_import",
      "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
      "function_name": "ZSTD_freeDCtx",
      "calling_convention": "stdcall",
      "charset": "ansi"
    },
    "return_type": "size_t",
    "parameters": [
      {
        "name": "dctx",
        "type": "ZSTD_DCtx*",
        "required": true,
        "description": "ZSTD_DCtx*"
      }
    ]
  },
  {
    "name": "ZSTD_freeDDict",
    "tool_id": "dll_ZSTD_freeDDict_130",
    "kind": "export",
    "ordinal": 130,
    "rva": "000647F0",
    "confidence": "guaranteed",
    "confidence_factors": {
      "has_signature": true,
      "has_documentation": true,
      "has_parameters": true,
      "has_return_type": true,
      "is_forwarded": true,
      "is_ordinal_only": false
    },
    "signature": {
      "return_type": "size_t",
      "parameters": "ZSTD_DDict* ddict",
      "calling_convention": null,
      "full_prototype": "ZSTDLIB_API size_t ZSTD_freeDDict(ZSTD_DDict* ddict)"
    },
    "documentation": {
      "summary": "! ZSTD_freeDDict() :\n Function frees memory allocated with ZSTD_createDDict()\n If a NULL pointer is passed, no operation is performed.",
      "description": "! ZSTD_freeDDict() :\n Function frees memory allocated with ZSTD_createDDict()\n If a NULL pointer is passed, no operation is performed.",
      "source_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "source_line": 1028
    },
    "evidence": {
      "discovered_by": "export_analyzer",
      "header_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "forwarded_to": null,
      "demangled_name": null
    },
    "mcp": {
      "version": "1.0",
      "input_schema": {
        "type": "object",
        "properties": {
          "ddict": {
            "type": "string",
            "description": "ZSTD_DDict*"
          }
        },
        "required": [
          "ddict"
        ]
      },
      "execution": {
        "method": "dll_import",
        "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
        "function_name": "ZSTD_freeDDict",
        "calling_convention": "stdcall",
        "charset": "ansi"
      }
    },
    "metadata": {
      "is_signed": false,
      "publisher": null
    },
    "execution": {
      "method": "dll_import",
      "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
      "function_name": "ZSTD_freeDDict",
      "calling_convention": "stdcall",
      "charset": "ansi"
    },
    "return_type": "size_t",
    "parameters": [
      {
        "name": "ddict",
        "type": "ZSTD_DDict*",
        "required": true,
        "description": "ZSTD_DDict*"
      }
    ]
  },
  {
    "name": "ZSTD_freeDStream",
    "tool_id": "dll_ZSTD_freeDStream_131",
    "kind": "export",
    "ordinal": 131,
    "rva": "00068560",
    "confidence": "guaranteed",
    "confidence_factors": {
      "has_signature": true,
      "has_documentation": true,
      "has_parameters": true,
      "has_return_type": true,
      "is_forwarded": true,
      "is_ordinal_only": false
    },
    "signature": {
      "return_type": "size_t",
      "parameters": "ZSTD_DStream* zds",
      "calling_convention": null,
      "full_prototype": "ZSTDLIB_API size_t ZSTD_freeDStream(ZSTD_DStream* zds)"
    },
    "documentation": {
      "summary": "accept NULL pointer",
      "description": "accept NULL pointer",
      "source_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "source_line": 912
    },
    "evidence": {
      "discovered_by": "export_analyzer",
      "header_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "forwarded_to": null,
      "demangled_name": null
    },
    "mcp": {
      "version": "1.0",
      "input_schema": {
        "type": "object",
        "properties": {
          "zds": {
            "type": "string",
            "description": "ZSTD_DStream*"
          }
        },
        "required": [
          "zds"
        ]
      },
      "execution": {
        "method": "dll_import",
        "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
        "function_name": "ZSTD_freeDStream",
        "calling_convention": "stdcall",
        "charset": "ansi"
      }
    },
    "metadata": {
      "is_signed": false,
      "publisher": null
    },
    "execution": {
      "method": "dll_import",
      "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
      "function_name": "ZSTD_freeDStream",
      "calling_convention": "stdcall",
      "charset": "ansi"
    },
    "return_type": "size_t",
    "parameters": [
      {
        "name": "zds",
        "type": "ZSTD_DStream*",
        "required": true,
        "description": "ZSTD_DStream*"
      }
    ]
  },
  {
    "name": "ZSTD_freeThreadPool",
    "tool_id": "dll_ZSTD_freeThreadPool_132",
    "kind": "export",
    "ordinal": 132,
    "rva": "00002E30",
    "confidence": "guaranteed",
    "confidence_factors": {
      "has_signature": true,
      "has_documentation": true,
      "has_parameters": true,
      "has_return_type": true,
      "is_forwarded": true,
      "is_ordinal_only": false
    },
    "signature": {
      "return_type": "void",
      "parameters": "ZSTD_threadPool* pool",
      "calling_convention": null,
      "full_prototype": "ZSTDLIB_STATIC_API void ZSTD_freeThreadPool (ZSTD_threadPool* pool)"
    },
    "documentation": {
      "summary": "accept NULL pointer",
      "description": "accept NULL pointer",
      "source_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "source_line": 1908
    },
    "evidence": {
      "discovered_by": "export_analyzer",
      "header_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "forwarded_to": null,
      "demangled_name": null
    },
    "mcp": {
      "version": "1.0",
      "input_schema": {
        "type": "object",
        "properties": {
          "pool": {
            "type": "string",
            "description": "ZSTD_threadPool*"
          }
        },
        "required": [
          "pool"
        ]
      },
      "execution": {
        "method": "dll_import",
        "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
        "function_name": "ZSTD_freeThreadPool",
        "calling_convention": "stdcall",
        "charset": "ansi"
      }
    },
    "metadata": {
      "is_signed": false,
      "publisher": null
    },
    "execution": {
      "method": "dll_import",
      "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
      "function_name": "ZSTD_freeThreadPool",
      "calling_convention": "stdcall",
      "charset": "ansi"
    },
    "return_type": "void",
    "parameters": [
      {
        "name": "pool",
        "type": "ZSTD_threadPool*",
        "required": true,
        "description": "ZSTD_threadPool*"
      }
    ]
  },
  {
    "name": "ZSTD_generateSequences",
    "tool_id": "dll_ZSTD_generateSequences_133",
    "kind": "export",
    "ordinal": 133,
    "rva": "00013410",
    "confidence": "guaranteed",
    "confidence_factors": {
      "has_signature": true,
      "has_documentation": false,
      "has_parameters": true,
      "has_return_type": true,
      "is_forwarded": true,
      "is_ordinal_only": false
    },
    "signature": {
      "return_type": "size_t",
      "parameters": "ZSTD_CCtx* zc, ZSTD_Sequence* outSeqs, size_t outSeqsCapacity, const void* src, size_t srcSize",
      "calling_convention": null,
      "full_prototype": "ZSTDLIB_STATIC_API size_t ZSTD_generateSequences(ZSTD_CCtx* zc, ZSTD_Sequence* outSeqs, size_t outSeqsCapacity, const void* src, size_t srcSize)"
    },
    "documentation": {
      "summary": "",
      "description": "",
      "source_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "source_line": 1625
    },
    "evidence": {
      "discovered_by": "export_analyzer",
      "header_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "forwarded_to": null,
      "demangled_name": null
    },
    "mcp": {
      "version": "1.0",
      "input_schema": {
        "type": "object",
        "properties": {
          "zc": {
            "type": "string",
            "description": "ZSTD_CCtx*"
          },
          "outSeqs": {
            "type": "string",
            "description": "ZSTD_Sequence*"
          },
          "outSeqsCapacity": {
            "type": "integer",
            "description": "size_t"
          },
          "src": {
            "type": "string",
            "description": "void*"
          },
          "srcSize": {
            "type": "integer",
            "description": "size_t"
          }
        },
        "required": [
          "zc",
          "outSeqs",
          "outSeqsCapacity",
          "src",
          "srcSize"
        ]
      },
      "execution": {
        "method": "dll_import",
        "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
        "function_name": "ZSTD_generateSequences",
        "calling_convention": "stdcall",
        "charset": "ansi"
      }
    },
    "metadata": {
      "is_signed": false,
      "publisher": null
    },
    "execution": {
      "method": "dll_import",
      "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
      "function_name": "ZSTD_generateSequences",
      "calling_convention": "stdcall",
      "charset": "ansi"
    },
    "return_type": "size_t",
    "parameters": [
      {
        "name": "zc",
        "type": "ZSTD_CCtx*",
        "required": true,
        "description": "ZSTD_CCtx*"
      },
      {
        "name": "outSeqs",
        "type": "ZSTD_Sequence*",
        "required": true,
        "description": "ZSTD_Sequence*"
      },
      {
        "name": "outSeqsCapacity",
        "type": "size_t",
        "required": true,
        "description": "size_t"
      },
      {
        "name": "src",
        "type": "const void*",
        "required": true,
        "description": "const void*"
      },
      {
        "name": "srcSize",
        "type": "size_t",
        "required": true,
        "description": "size_t"
      }
    ]
  },
  {
    "name": "ZSTD_getBlockSize",
    "tool_id": "dll_ZSTD_getBlockSize_134",
    "kind": "export",
    "ordinal": 134,
    "rva": "00013550",
    "confidence": "medium",
    "confidence_factors": {
      "has_signature": false,
      "has_documentation": false,
      "has_parameters": false,
      "has_return_type": false,
      "is_forwarded": true,
      "is_ordinal_only": false
    },
    "signature": {
      "return_type": null,
      "parameters": "",
      "calling_convention": null,
      "full_prototype": "void ZSTD_getBlockSize()"
    },
    "documentation": {
      "summary": null,
      "description": null,
      "source_file": null,
      "source_line": null
    },
    "evidence": {
      "discovered_by": "export_analyzer",
      "header_file": null,
      "forwarded_to": null,
      "demangled_name": null
    },
    "mcp": {
      "version": "1.0",
      "input_schema": {
        "type": "object",
        "properties": {},
        "required": []
      },
      "execution": {
        "method": "dll_import",
        "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
        "function_name": "ZSTD_getBlockSize",
        "calling_convention": "stdcall",
        "charset": "ansi"
      }
    },
    "metadata": {
      "is_signed": false,
      "publisher": null
    },
    "execution": {
      "method": "dll_import",
      "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
      "function_name": "ZSTD_getBlockSize",
      "calling_convention": "stdcall",
      "charset": "ansi"
    }
  },
  {
    "name": "ZSTD_getCParams",
    "tool_id": "dll_ZSTD_getCParams_135",
    "kind": "export",
    "ordinal": 135,
    "rva": "00013580",
    "confidence": "guaranteed",
    "confidence_factors": {
      "has_signature": true,
      "has_documentation": true,
      "has_parameters": true,
      "has_return_type": true,
      "is_forwarded": true,
      "is_ordinal_only": false
    },
    "signature": {
      "return_type": "ZSTD_compressionParameters",
      "parameters": "int compressionLevel, unsigned long long estimatedSrcSize, size_t dictSize",
      "calling_convention": null,
      "full_prototype": "ZSTDLIB_STATIC_API ZSTD_compressionParameters ZSTD_getCParams(int compressionLevel, unsigned long long estimatedSrcSize, size_t dictSize)"
    },
    "documentation": {
      "summary": "! ZSTD_getCParams() :\n@return ZSTD_compressionParameters structure for a selected compression level and estimated srcSize.\n`estimatedSrcSize` value is optional, select 0 if not known",
      "description": "! ZSTD_getCParams() :\n@return ZSTD_compressionParameters structure for a selected compression level and estimated srcSize.\n`estimatedSrcSize` value is optional, select 0 if not known",
      "source_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "source_line": 1944
    },
    "evidence": {
      "discovered_by": "export_analyzer",
      "header_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "forwarded_to": null,
      "demangled_name": null
    },
    "mcp": {
      "version": "1.0",
      "input_schema": {
        "type": "object",
        "properties": {
          "compressionLevel": {
            "type": "integer",
            "description": "int"
          },
          "estimatedSrcSize": {
            "type": "integer",
            "description": "unsigned long long"
          },
          "dictSize": {
            "type": "integer",
            "description": "size_t"
          }
        },
        "required": [
          "compressionLevel",
          "estimatedSrcSize",
          "dictSize"
        ]
      },
      "execution": {
        "method": "dll_import",
        "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
        "function_name": "ZSTD_getCParams",
        "calling_convention": "stdcall",
        "charset": "ansi"
      }
    },
    "metadata": {
      "is_signed": false,
      "publisher": null
    },
    "execution": {
      "method": "dll_import",
      "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
      "function_name": "ZSTD_getCParams",
      "calling_convention": "stdcall",
      "charset": "ansi"
    },
    "return_type": "ZSTD_compressionParameters",
    "parameters": [
      {
        "name": "compressionLevel",
        "type": "int",
        "required": true,
        "description": "int"
      },
      {
        "name": "estimatedSrcSize",
        "type": "unsigned long long",
        "required": true,
        "description": "unsigned long long"
      },
      {
        "name": "dictSize",
        "type": "size_t",
        "required": true,
        "description": "size_t"
      }
    ]
  },
  {
    "name": "ZSTD_getDecompressedSize",
    "tool_id": "dll_ZSTD_getDecompressedSize_136",
    "kind": "export",
    "ordinal": 136,
    "rva": "000685C0",
    "confidence": "guaranteed",
    "confidence_factors": {
      "has_signature": true,
      "has_documentation": false,
      "has_parameters": true,
      "has_return_type": true,
      "is_forwarded": true,
      "is_ordinal_only": false
    },
    "signature": {
      "return_type": "unsigned long long",
      "parameters": "const void* src, size_t srcSize",
      "calling_convention": null,
      "full_prototype": "ZSTDLIB_API unsigned long long ZSTD_getDecompressedSize(const void* src, size_t srcSize)"
    },
    "documentation": {
      "summary": "",
      "description": "",
      "source_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "source_line": 214
    },
    "evidence": {
      "discovered_by": "export_analyzer",
      "header_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "forwarded_to": null,
      "demangled_name": null
    },
    "mcp": {
      "version": "1.0",
      "input_schema": {
        "type": "object",
        "properties": {
          "src": {
            "type": "string",
            "description": "void*"
          },
          "srcSize": {
            "type": "integer",
            "description": "size_t"
          }
        },
        "required": [
          "src",
          "srcSize"
        ]
      },
      "execution": {
        "method": "dll_import",
        "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
        "function_name": "ZSTD_getDecompressedSize",
        "calling_convention": "stdcall",
        "charset": "ansi"
      }
    },
    "metadata": {
      "is_signed": false,
      "publisher": null
    },
    "execution": {
      "method": "dll_import",
      "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
      "function_name": "ZSTD_getDecompressedSize",
      "calling_convention": "stdcall",
      "charset": "ansi"
    },
    "return_type": "unsigned long long",
    "parameters": [
      {
        "name": "src",
        "type": "const void*",
        "required": true,
        "description": "const void*"
      },
      {
        "name": "srcSize",
        "type": "size_t",
        "required": true,
        "description": "size_t"
      }
    ]
  },
  {
    "name": "ZSTD_getDictID_fromCDict",
    "tool_id": "dll_ZSTD_getDictID_fromCDict_137",
    "kind": "export",
    "ordinal": 137,
    "rva": "00013870",
    "confidence": "guaranteed",
    "confidence_factors": {
      "has_signature": true,
      "has_documentation": true,
      "has_parameters": true,
      "has_return_type": true,
      "is_forwarded": true,
      "is_ordinal_only": false
    },
    "signature": {
      "return_type": "unsigned",
      "parameters": "const ZSTD_CDict* cdict",
      "calling_convention": null,
      "full_prototype": "ZSTDLIB_API unsigned ZSTD_getDictID_fromCDict(const ZSTD_CDict* cdict)"
    },
    "documentation": {
      "summary": "! ZSTD_getDictID_fromCDict() : Requires v1.5.0+\n Provides the dictID of the dictionary loaded into `cdict`.\n If @return == 0, the dictionary is not conformant to Zstandard specification, or empty.\n Non-conformant dictionaries can still be loaded, but as content-only dictionaries.",
      "description": "! ZSTD_getDictID_fromCDict() : Requires v1.5.0+\n Provides the dictID of the dictionary loaded into `cdict`.\n If @return == 0, the dictionary is not conformant to Zstandard specification, or empty.\n Non-conformant dictionaries can still be loaded, but as content-only dictionaries.",
      "source_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "source_line": 1053
    },
    "evidence": {
      "discovered_by": "export_analyzer",
      "header_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "forwarded_to": null,
      "demangled_name": null
    },
    "mcp": {
      "version": "1.0",
      "input_schema": {
        "type": "object",
        "properties": {
          "cdict": {
            "type": "string",
            "description": "ZSTD_CDict*"
          }
        },
        "required": [
          "cdict"
        ]
      },
      "execution": {
        "method": "dll_import",
        "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
        "function_name": "ZSTD_getDictID_fromCDict",
        "calling_convention": "stdcall",
        "charset": "ansi"
      }
    },
    "metadata": {
      "is_signed": false,
      "publisher": null
    },
    "execution": {
      "method": "dll_import",
      "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
      "function_name": "ZSTD_getDictID_fromCDict",
      "calling_convention": "stdcall",
      "charset": "ansi"
    },
    "return_type": "unsigned",
    "parameters": [
      {
        "name": "cdict",
        "type": "const ZSTD_CDict*",
        "required": true,
        "description": "const ZSTD_CDict*"
      }
    ]
  },
  {
    "name": "ZSTD_getDictID_fromDDict",
    "tool_id": "dll_ZSTD_getDictID_fromDDict_138",
    "kind": "export",
    "ordinal": 138,
    "rva": "00064890",
    "confidence": "guaranteed",
    "confidence_factors": {
      "has_signature": true,
      "has_documentation": true,
      "has_parameters": true,
      "has_return_type": true,
      "is_forwarded": true,
      "is_ordinal_only": false
    },
    "signature": {
      "return_type": "unsigned",
      "parameters": "const ZSTD_DDict* ddict",
      "calling_convention": null,
      "full_prototype": "ZSTDLIB_API unsigned ZSTD_getDictID_fromDDict(const ZSTD_DDict* ddict)"
    },
    "documentation": {
      "summary": "! ZSTD_getDictID_fromDDict() : Requires v1.4.0+\n Provides the dictID of the dictionary loaded into `ddict`.\n If @return == 0, the dictionary is not conformant to Zstandard specification, or empty.\n Non-conformant dictionaries can still be loaded, but as content-only dictionaries.",
      "description": "! ZSTD_getDictID_fromDDict() : Requires v1.4.0+\n Provides the dictID of the dictionary loaded into `ddict`.\n If @return == 0, the dictionary is not conformant to Zstandard specification, or empty.\n Non-conformant dictionaries can still be loaded, but as content-only dictionaries.",
      "source_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "source_line": 1059
    },
    "evidence": {
      "discovered_by": "export_analyzer",
      "header_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "forwarded_to": null,
      "demangled_name": null
    },
    "mcp": {
      "version": "1.0",
      "input_schema": {
        "type": "object",
        "properties": {
          "ddict": {
            "type": "string",
            "description": "ZSTD_DDict*"
          }
        },
        "required": [
          "ddict"
        ]
      },
      "execution": {
        "method": "dll_import",
        "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
        "function_name": "ZSTD_getDictID_fromDDict",
        "calling_convention": "stdcall",
        "charset": "ansi"
      }
    },
    "metadata": {
      "is_signed": false,
      "publisher": null
    },
    "execution": {
      "method": "dll_import",
      "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
      "function_name": "ZSTD_getDictID_fromDDict",
      "calling_convention": "stdcall",
      "charset": "ansi"
    },
    "return_type": "unsigned",
    "parameters": [
      {
        "name": "ddict",
        "type": "const ZSTD_DDict*",
        "required": true,
        "description": "const ZSTD_DDict*"
      }
    ]
  },
  {
    "name": "ZSTD_getDictID_fromDict",
    "tool_id": "dll_ZSTD_getDictID_fromDict_139",
    "kind": "export",
    "ordinal": 139,
    "rva": "000685E0",
    "confidence": "guaranteed",
    "confidence_factors": {
      "has_signature": true,
      "has_documentation": true,
      "has_parameters": true,
      "has_return_type": true,
      "is_forwarded": true,
      "is_ordinal_only": false
    },
    "signature": {
      "return_type": "unsigned",
      "parameters": "const void* dict, size_t dictSize",
      "calling_convention": null,
      "full_prototype": "ZSTDLIB_API unsigned ZSTD_getDictID_fromDict(const void* dict, size_t dictSize)"
    },
    "documentation": {
      "summary": "! ZSTD_getDictID_fromDict() : Requires v1.4.0+\n Provides the dictID stored within dictionary.\n if @return == 0, the dictionary is not conformant with Zstandard specification.\n It can still be loaded, but as a content-only dictionary.",
      "description": "! ZSTD_getDictID_fromDict() : Requires v1.4.0+\n Provides the dictID stored within dictionary.\n if @return == 0, the dictionary is not conformant with Zstandard specification.\n It can still be loaded, but as a content-only dictionary.",
      "source_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "source_line": 1047
    },
    "evidence": {
      "discovered_by": "export_analyzer",
      "header_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "forwarded_to": null,
      "demangled_name": null
    },
    "mcp": {
      "version": "1.0",
      "input_schema": {
        "type": "object",
        "properties": {
          "dict": {
            "type": "string",
            "description": "void*"
          },
          "dictSize": {
            "type": "integer",
            "description": "size_t"
          }
        },
        "required": [
          "dict",
          "dictSize"
        ]
      },
      "execution": {
        "method": "dll_import",
        "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
        "function_name": "ZSTD_getDictID_fromDict",
        "calling_convention": "stdcall",
        "charset": "ansi"
      }
    },
    "metadata": {
      "is_signed": false,
      "publisher": null
    },
    "execution": {
      "method": "dll_import",
      "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
      "function_name": "ZSTD_getDictID_fromDict",
      "calling_convention": "stdcall",
      "charset": "ansi"
    },
    "return_type": "unsigned",
    "parameters": [
      {
        "name": "dict",
        "type": "const void*",
        "required": true,
        "description": "const void*"
      },
      {
        "name": "dictSize",
        "type": "size_t",
        "required": true,
        "description": "size_t"
      }
    ]
  },
  {
    "name": "ZSTD_getDictID_fromFrame",
    "tool_id": "dll_ZSTD_getDictID_fromFrame_140",
    "kind": "export",
    "ordinal": 140,
    "rva": "00068600",
    "confidence": "guaranteed",
    "confidence_factors": {
      "has_signature": true,
      "has_documentation": true,
      "has_parameters": true,
      "has_return_type": true,
      "is_forwarded": true,
      "is_ordinal_only": false
    },
    "signature": {
      "return_type": "unsigned",
      "parameters": "const void* src, size_t srcSize",
      "calling_convention": null,
      "full_prototype": "ZSTDLIB_API unsigned ZSTD_getDictID_fromFrame(const void* src, size_t srcSize)"
    },
    "documentation": {
      "summary": "! ZSTD_getDictID_fromFrame() : Requires v1.4.0+\n Provides the dictID required to decompressed the frame stored within `src`.\n If @return == 0, the dictID could not be decoded.\n This could for one of the following reasons :\n - The frame does not require a dictionary to be decoded (most common case).\n - The frame was built with dictID intentionally removed. Whatever dictionary is necessary is a hidden piece of information.\n   Note : this use case also happens when using a non-conformant dictionary.\n - `srcSize` is too small, and as a result, the frame header could not be decoded (only possible if `srcSize < ZSTD_FRAMEHEADERSIZE_MAX`).\n - This is not a Zstandard frame.\n When identifying the exact failure cause, it's possible to use ZSTD_getFrameHeader(), which will provide a more precise error code.",
      "description": "! ZSTD_getDictID_fromFrame() : Requires v1.4.0+\n Provides the dictID required to decompressed the frame stored within `src`.\n If @return == 0, the dictID could not be decoded.\n This could for one of the following reasons :\n - The frame does not require a dictionary to be decoded (most common case).\n - The frame was built with dictID intentionally removed. Whatever dictionary is necessary is a hidden piece of information.\n   Note : this use case also happens when using a non-conformant dictionary.\n - `srcSize` is too small, and as a result, the frame header could not be decoded (only possible if `srcSize < ZSTD_FRAMEHEADERSIZE_MAX`).\n - This is not a Zstandard frame.\n When identifying the exact failure cause, it's possible to use ZSTD_getFrameHeader(), which will provide a more precise error code.",
      "source_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "source_line": 1071
    },
    "evidence": {
      "discovered_by": "export_analyzer",
      "header_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "forwarded_to": null,
      "demangled_name": null
    },
    "mcp": {
      "version": "1.0",
      "input_schema": {
        "type": "object",
        "properties": {
          "src": {
            "type": "string",
            "description": "void*"
          },
          "srcSize": {
            "type": "integer",
            "description": "size_t"
          }
        },
        "required": [
          "src",
          "srcSize"
        ]
      },
      "execution": {
        "method": "dll_import",
        "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
        "function_name": "ZSTD_getDictID_fromFrame",
        "calling_convention": "stdcall",
        "charset": "ansi"
      }
    },
    "metadata": {
      "is_signed": false,
      "publisher": null
    },
    "execution": {
      "method": "dll_import",
      "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
      "function_name": "ZSTD_getDictID_fromFrame",
      "calling_convention": "stdcall",
      "charset": "ansi"
    },
    "return_type": "unsigned",
    "parameters": [
      {
        "name": "src",
        "type": "const void*",
        "required": true,
        "description": "const void*"
      },
      {
        "name": "srcSize",
        "type": "size_t",
        "required": true,
        "description": "size_t"
      }
    ]
  },
  {
    "name": "ZSTD_getErrorCode",
    "tool_id": "dll_ZSTD_getErrorCode_141",
    "kind": "export",
    "ordinal": 141,
    "rva": "000035D0",
    "confidence": "guaranteed",
    "confidence_factors": {
      "has_signature": true,
      "has_documentation": true,
      "has_parameters": true,
      "has_return_type": true,
      "is_forwarded": true,
      "is_ordinal_only": false
    },
    "signature": {
      "return_type": "ZSTD_ErrorCode",
      "parameters": "size_t functionResult",
      "calling_convention": null,
      "full_prototype": "ZSTDLIB_API ZSTD_ErrorCode ZSTD_getErrorCode(size_t functionResult)"
    },
    "documentation": {
      "summary": "convert a result into an error code, which can be compared to error enum list",
      "description": "convert a result into an error code, which can be compared to error enum list",
      "source_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "source_line": 260
    },
    "evidence": {
      "discovered_by": "export_analyzer",
      "header_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "forwarded_to": null,
      "demangled_name": null
    },
    "mcp": {
      "version": "1.0",
      "input_schema": {
        "type": "object",
        "properties": {
          "functionResult": {
            "type": "integer",
            "description": "size_t"
          }
        },
        "required": [
          "functionResult"
        ]
      },
      "execution": {
        "method": "dll_import",
        "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
        "function_name": "ZSTD_getErrorCode",
        "calling_convention": "stdcall",
        "charset": "ansi"
      }
    },
    "metadata": {
      "is_signed": false,
      "publisher": null
    },
    "execution": {
      "method": "dll_import",
      "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
      "function_name": "ZSTD_getErrorCode",
      "calling_convention": "stdcall",
      "charset": "ansi"
    },
    "return_type": "ZSTD_ErrorCode",
    "parameters": [
      {
        "name": "functionResult",
        "type": "size_t",
        "required": true,
        "description": "size_t"
      }
    ]
  },
  {
    "name": "ZSTD_getErrorName",
    "tool_id": "dll_ZSTD_getErrorName_142",
    "kind": "export",
    "ordinal": 142,
    "rva": "000035E0",
    "confidence": "guaranteed",
    "confidence_factors": {
      "has_signature": true,
      "has_documentation": true,
      "has_parameters": true,
      "has_return_type": true,
      "is_forwarded": true,
      "is_ordinal_only": false
    },
    "signature": {
      "return_type": "const char*",
      "parameters": "size_t result",
      "calling_convention": null,
      "full_prototype": "ZSTDLIB_API const char* ZSTD_getErrorName(size_t result)"
    },
    "documentation": {
      "summary": "!< provides readable string from a function result",
      "description": "!< provides readable string from a function result",
      "source_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "source_line": 261
    },
    "evidence": {
      "discovered_by": "export_analyzer",
      "header_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "forwarded_to": null,
      "demangled_name": null
    },
    "mcp": {
      "version": "1.0",
      "input_schema": {
        "type": "object",
        "properties": {
          "result": {
            "type": "integer",
            "description": "size_t"
          }
        },
        "required": [
          "result"
        ]
      },
      "execution": {
        "method": "dll_import",
        "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
        "function_name": "ZSTD_getErrorName",
        "calling_convention": "stdcall",
        "charset": "ansi"
      }
    },
    "metadata": {
      "is_signed": false,
      "publisher": null
    },
    "execution": {
      "method": "dll_import",
      "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
      "function_name": "ZSTD_getErrorName",
      "calling_convention": "stdcall",
      "charset": "ansi"
    },
    "return_type": "const char*",
    "parameters": [
      {
        "name": "result",
        "type": "size_t",
        "required": true,
        "description": "size_t"
      }
    ]
  },
  {
    "name": "ZSTD_getErrorString",
    "tool_id": "dll_ZSTD_getErrorString_143",
    "kind": "export",
    "ordinal": 143,
    "rva": "00003600",
    "confidence": "guaranteed",
    "confidence_factors": {
      "has_signature": true,
      "has_documentation": true,
      "has_parameters": true,
      "has_return_type": true,
      "is_forwarded": true,
      "is_ordinal_only": false
    },
    "signature": {
      "return_type": "const char*",
      "parameters": "ZSTD_ErrorCode code",
      "calling_convention": null,
      "full_prototype": "ZSTDERRORLIB_API const char* ZSTD_getErrorString(ZSTD_ErrorCode code)"
    },
    "documentation": {
      "summary": "*< Same as ZSTD_getErrorName, but using a `ZSTD_ErrorCode` enum argument",
      "description": "*< Same as ZSTD_getErrorName, but using a `ZSTD_ErrorCode` enum argument",
      "source_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd_errors.h",
      "source_line": 100
    },
    "evidence": {
      "discovered_by": "export_analyzer",
      "header_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd_errors.h",
      "forwarded_to": null,
      "demangled_name": null
    },
    "mcp": {
      "version": "1.0",
      "input_schema": {
        "type": "object",
        "properties": {
          "code": {
            "type": "string",
            "description": "ZSTD_ErrorCode"
          }
        },
        "required": [
          "code"
        ]
      },
      "execution": {
        "method": "dll_import",
        "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
        "function_name": "ZSTD_getErrorString",
        "calling_convention": "stdcall",
        "charset": "ansi"
      }
    },
    "metadata": {
      "is_signed": false,
      "publisher": null
    },
    "execution": {
      "method": "dll_import",
      "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
      "function_name": "ZSTD_getErrorString",
      "calling_convention": "stdcall",
      "charset": "ansi"
    },
    "return_type": "const char*",
    "parameters": [
      {
        "name": "code",
        "type": "ZSTD_ErrorCode",
        "required": true,
        "description": "ZSTD_ErrorCode"
      }
    ]
  },
  {
    "name": "ZSTD_getFrameContentSize",
    "tool_id": "dll_ZSTD_getFrameContentSize_144",
    "kind": "export",
    "ordinal": 144,
    "rva": "00068650",
    "confidence": "guaranteed",
    "confidence_factors": {
      "has_signature": true,
      "has_documentation": false,
      "has_parameters": true,
      "has_return_type": true,
      "is_forwarded": true,
      "is_ordinal_only": false
    },
    "signature": {
      "return_type": "unsigned long long",
      "parameters": "const void *src, size_t srcSize",
      "calling_convention": null,
      "full_prototype": "ZSTDLIB_API unsigned long long ZSTD_getFrameContentSize(const void *src, size_t srcSize)"
    },
    "documentation": {
      "summary": "",
      "description": "",
      "source_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "source_line": 205
    },
    "evidence": {
      "discovered_by": "export_analyzer",
      "header_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "forwarded_to": null,
      "demangled_name": null
    },
    "mcp": {
      "version": "1.0",
      "input_schema": {
        "type": "object",
        "properties": {
          "src": {
            "type": "string",
            "description": "void"
          },
          "srcSize": {
            "type": "integer",
            "description": "size_t"
          }
        },
        "required": [
          "src",
          "srcSize"
        ]
      },
      "execution": {
        "method": "dll_import",
        "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
        "function_name": "ZSTD_getFrameContentSize",
        "calling_convention": "stdcall",
        "charset": "ansi"
      }
    },
    "metadata": {
      "is_signed": false,
      "publisher": null
    },
    "execution": {
      "method": "dll_import",
      "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
      "function_name": "ZSTD_getFrameContentSize",
      "calling_convention": "stdcall",
      "charset": "ansi"
    },
    "return_type": "unsigned long long",
    "parameters": [
      {
        "name": "src",
        "type": "const void",
        "required": true,
        "description": "const void"
      },
      {
        "name": "srcSize",
        "type": "size_t",
        "required": true,
        "description": "size_t"
      }
    ]
  },
  {
    "name": "ZSTD_getFrameHeader",
    "tool_id": "dll_ZSTD_getFrameHeader_145",
    "kind": "export",
    "ordinal": 145,
    "rva": "00068750",
    "confidence": "guaranteed",
    "confidence_factors": {
      "has_signature": true,
      "has_documentation": true,
      "has_parameters": true,
      "has_return_type": true,
      "is_forwarded": true,
      "is_ordinal_only": false
    },
    "signature": {
      "return_type": "size_t",
      "parameters": "ZSTD_FrameHeader* zfhPtr, const void* src, size_t srcSize",
      "calling_convention": null,
      "full_prototype": "ZSTDLIB_STATIC_API size_t ZSTD_getFrameHeader(ZSTD_FrameHeader* zfhPtr, const void* src, size_t srcSize)"
    },
    "documentation": {
      "summary": "! ZSTD_getFrameHeader() :\n decode Frame Header into `zfhPtr`, or requires larger `srcSize`.\n@return : 0 => header is complete, `zfhPtr` is correctly filled,\n         >0 => `srcSize` is too small, @return value is the wanted `srcSize` amount, `zfhPtr` is not filled,\n          or an error code, which can be tested using ZSTD_isError()",
      "description": "! ZSTD_getFrameHeader() :\n decode Frame Header into `zfhPtr`, or requires larger `srcSize`.\n@return : 0 => header is complete, `zfhPtr` is correctly filled,\n         >0 => `srcSize` is too small, @return value is the wanted `srcSize` amount, `zfhPtr` is not filled,\n          or an error code, which can be tested using ZSTD_isError()",
      "source_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "source_line": 1530
    },
    "evidence": {
      "discovered_by": "export_analyzer",
      "header_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "forwarded_to": null,
      "demangled_name": null
    },
    "mcp": {
      "version": "1.0",
      "input_schema": {
        "type": "object",
        "properties": {
          "zfhPtr": {
            "type": "string",
            "description": "ZSTD_FrameHeader*"
          },
          "src": {
            "type": "string",
            "description": "void*"
          },
          "srcSize": {
            "type": "integer",
            "description": "size_t"
          }
        },
        "required": [
          "zfhPtr",
          "src",
          "srcSize"
        ]
      },
      "execution": {
        "method": "dll_import",
        "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
        "function_name": "ZSTD_getFrameHeader",
        "calling_convention": "stdcall",
        "charset": "ansi"
      }
    },
    "metadata": {
      "is_signed": false,
      "publisher": null
    },
    "execution": {
      "method": "dll_import",
      "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
      "function_name": "ZSTD_getFrameHeader",
      "calling_convention": "stdcall",
      "charset": "ansi"
    },
    "return_type": "size_t",
    "parameters": [
      {
        "name": "zfhPtr",
        "type": "ZSTD_FrameHeader*",
        "required": true,
        "description": "ZSTD_FrameHeader*"
      },
      {
        "name": "src",
        "type": "const void*",
        "required": true,
        "description": "const void*"
      },
      {
        "name": "srcSize",
        "type": "size_t",
        "required": true,
        "description": "size_t"
      }
    ]
  },
  {
    "name": "ZSTD_getFrameHeader_advanced",
    "tool_id": "dll_ZSTD_getFrameHeader_advanced_146",
    "kind": "export",
    "ordinal": 146,
    "rva": "00068760",
    "confidence": "guaranteed",
    "confidence_factors": {
      "has_signature": true,
      "has_documentation": true,
      "has_parameters": true,
      "has_return_type": true,
      "is_forwarded": true,
      "is_ordinal_only": false
    },
    "signature": {
      "return_type": "size_t",
      "parameters": "ZSTD_FrameHeader* zfhPtr, const void* src, size_t srcSize, ZSTD_format_e format",
      "calling_convention": null,
      "full_prototype": "ZSTDLIB_STATIC_API size_t ZSTD_getFrameHeader_advanced(ZSTD_FrameHeader* zfhPtr, const void* src, size_t srcSize, ZSTD_format_e format)"
    },
    "documentation": {
      "summary": "! ZSTD_getFrameHeader_advanced() :\n same as ZSTD_getFrameHeader(),\n with added capability to select a format (like ZSTD_f_zstd1_magicless)",
      "description": "! ZSTD_getFrameHeader_advanced() :\n same as ZSTD_getFrameHeader(),\n with added capability to select a format (like ZSTD_f_zstd1_magicless)",
      "source_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "source_line": 1534
    },
    "evidence": {
      "discovered_by": "export_analyzer",
      "header_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "forwarded_to": null,
      "demangled_name": null
    },
    "mcp": {
      "version": "1.0",
      "input_schema": {
        "type": "object",
        "properties": {
          "zfhPtr": {
            "type": "string",
            "description": "ZSTD_FrameHeader*"
          },
          "src": {
            "type": "string",
            "description": "void*"
          },
          "srcSize": {
            "type": "integer",
            "description": "size_t"
          },
          "format": {
            "type": "string",
            "description": "ZSTD_format_e"
          }
        },
        "required": [
          "zfhPtr",
          "src",
          "srcSize",
          "format"
        ]
      },
      "execution": {
        "method": "dll_import",
        "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
        "function_name": "ZSTD_getFrameHeader_advanced",
        "calling_convention": "stdcall",
        "charset": "ansi"
      }
    },
    "metadata": {
      "is_signed": false,
      "publisher": null
    },
    "execution": {
      "method": "dll_import",
      "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
      "function_name": "ZSTD_getFrameHeader_advanced",
      "calling_convention": "stdcall",
      "charset": "ansi"
    },
    "return_type": "size_t",
    "parameters": [
      {
        "name": "zfhPtr",
        "type": "ZSTD_FrameHeader*",
        "required": true,
        "description": "ZSTD_FrameHeader*"
      },
      {
        "name": "src",
        "type": "const void*",
        "required": true,
        "description": "const void*"
      },
      {
        "name": "srcSize",
        "type": "size_t",
        "required": true,
        "description": "size_t"
      },
      {
        "name": "format",
        "type": "ZSTD_format_e",
        "required": true,
        "description": "ZSTD_format_e"
      }
    ]
  },
  {
    "name": "ZSTD_getFrameProgression",
    "tool_id": "dll_ZSTD_getFrameProgression_147",
    "kind": "export",
    "ordinal": 147,
    "rva": "00013880",
    "confidence": "guaranteed",
    "confidence_factors": {
      "has_signature": true,
      "has_documentation": false,
      "has_parameters": true,
      "has_return_type": true,
      "is_forwarded": true,
      "is_ordinal_only": false
    },
    "signature": {
      "return_type": "ZSTD_frameProgression",
      "parameters": "const ZSTD_CCtx* cctx",
      "calling_convention": null,
      "full_prototype": "ZSTDLIB_STATIC_API ZSTD_frameProgression ZSTD_getFrameProgression(const ZSTD_CCtx* cctx)"
    },
    "documentation": {
      "summary": "",
      "description": "",
      "source_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "source_line": 2751
    },
    "evidence": {
      "discovered_by": "export_analyzer",
      "header_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "forwarded_to": null,
      "demangled_name": null
    },
    "mcp": {
      "version": "1.0",
      "input_schema": {
        "type": "object",
        "properties": {
          "cctx": {
            "type": "string",
            "description": "ZSTD_CCtx*"
          }
        },
        "required": [
          "cctx"
        ]
      },
      "execution": {
        "method": "dll_import",
        "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
        "function_name": "ZSTD_getFrameProgression",
        "calling_convention": "stdcall",
        "charset": "ansi"
      }
    },
    "metadata": {
      "is_signed": false,
      "publisher": null
    },
    "execution": {
      "method": "dll_import",
      "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
      "function_name": "ZSTD_getFrameProgression",
      "calling_convention": "stdcall",
      "charset": "ansi"
    },
    "return_type": "ZSTD_frameProgression",
    "parameters": [
      {
        "name": "cctx",
        "type": "const ZSTD_CCtx*",
        "required": true,
        "description": "const ZSTD_CCtx*"
      }
    ]
  },
  {
    "name": "ZSTD_getParams",
    "tool_id": "dll_ZSTD_getParams_148",
    "kind": "export",
    "ordinal": 148,
    "rva": "00013910",
    "confidence": "guaranteed",
    "confidence_factors": {
      "has_signature": true,
      "has_documentation": true,
      "has_parameters": true,
      "has_return_type": true,
      "is_forwarded": true,
      "is_ordinal_only": false
    },
    "signature": {
      "return_type": "ZSTD_parameters",
      "parameters": "int compressionLevel, unsigned long long estimatedSrcSize, size_t dictSize",
      "calling_convention": null,
      "full_prototype": "ZSTDLIB_STATIC_API ZSTD_parameters ZSTD_getParams(int compressionLevel, unsigned long long estimatedSrcSize, size_t dictSize)"
    },
    "documentation": {
      "summary": "! ZSTD_getParams() :\n same as ZSTD_getCParams(), but @return a full `ZSTD_parameters` object instead of sub-component `ZSTD_compressionParameters`.\n All fields of `ZSTD_frameParameters` are set to default : contentSize=1, checksum=0, noDictID=0",
      "description": "! ZSTD_getParams() :\n same as ZSTD_getCParams(), but @return a full `ZSTD_parameters` object instead of sub-component `ZSTD_compressionParameters`.\n All fields of `ZSTD_frameParameters` are set to default : contentSize=1, checksum=0, noDictID=0",
      "source_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "source_line": 1949
    },
    "evidence": {
      "discovered_by": "export_analyzer",
      "header_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "forwarded_to": null,
      "demangled_name": null
    },
    "mcp": {
      "version": "1.0",
      "input_schema": {
        "type": "object",
        "properties": {
          "compressionLevel": {
            "type": "integer",
            "description": "int"
          },
          "estimatedSrcSize": {
            "type": "integer",
            "description": "unsigned long long"
          },
          "dictSize": {
            "type": "integer",
            "description": "size_t"
          }
        },
        "required": [
          "compressionLevel",
          "estimatedSrcSize",
          "dictSize"
        ]
      },
      "execution": {
        "method": "dll_import",
        "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
        "function_name": "ZSTD_getParams",
        "calling_convention": "stdcall",
        "charset": "ansi"
      }
    },
    "metadata": {
      "is_signed": false,
      "publisher": null
    },
    "execution": {
      "method": "dll_import",
      "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
      "function_name": "ZSTD_getParams",
      "calling_convention": "stdcall",
      "charset": "ansi"
    },
    "return_type": "ZSTD_parameters",
    "parameters": [
      {
        "name": "compressionLevel",
        "type": "int",
        "required": true,
        "description": "int"
      },
      {
        "name": "estimatedSrcSize",
        "type": "unsigned long long",
        "required": true,
        "description": "unsigned long long"
      },
      {
        "name": "dictSize",
        "type": "size_t",
        "required": true,
        "description": "size_t"
      }
    ]
  },
  {
    "name": "ZSTD_initCStream",
    "tool_id": "dll_ZSTD_initCStream_149",
    "kind": "export",
    "ordinal": 149,
    "rva": "00013C50",
    "confidence": "guaranteed",
    "confidence_factors": {
      "has_signature": true,
      "has_documentation": true,
      "has_parameters": true,
      "has_return_type": true,
      "is_forwarded": true,
      "is_ordinal_only": false
    },
    "signature": {
      "return_type": "size_t",
      "parameters": "ZSTD_CStream* zcs, int compressionLevel",
      "calling_convention": null,
      "full_prototype": "ZSTDLIB_API size_t ZSTD_initCStream(ZSTD_CStream* zcs, int compressionLevel)"
    },
    "documentation": {
      "summary": "!\nEquivalent to:\n\n    ZSTD_CCtx_reset(zcs, ZSTD_reset_session_only);\n    ZSTD_CCtx_refCDict(zcs, NULL); // clear the dictionary (if any)\n    ZSTD_CCtx_setParameter(zcs, ZSTD_c_compressionLevel, compressionLevel);\n\nNote that ZSTD_initCStream() clears any previously set dictionary. Use the new API\nto compress with a dictionary.",
      "description": "!\nEquivalent to:\n\n    ZSTD_CCtx_reset(zcs, ZSTD_reset_session_only);\n    ZSTD_CCtx_refCDict(zcs, NULL); // clear the dictionary (if any)\n    ZSTD_CCtx_setParameter(zcs, ZSTD_c_compressionLevel, compressionLevel);\n\nNote that ZSTD_initCStream() clears any previously set dictionary. Use the new API\nto compress with a dictionary.",
      "source_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "source_line": 862
    },
    "evidence": {
      "discovered_by": "export_analyzer",
      "header_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "forwarded_to": null,
      "demangled_name": null
    },
    "mcp": {
      "version": "1.0",
      "input_schema": {
        "type": "object",
        "properties": {
          "zcs": {
            "type": "string",
            "description": "ZSTD_CStream*"
          },
          "compressionLevel": {
            "type": "integer",
            "description": "int"
          }
        },
        "required": [
          "zcs",
          "compressionLevel"
        ]
      },
      "execution": {
        "method": "dll_import",
        "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
        "function_name": "ZSTD_initCStream",
        "calling_convention": "stdcall",
        "charset": "ansi"
      }
    },
    "metadata": {
      "is_signed": false,
      "publisher": null
    },
    "execution": {
      "method": "dll_import",
      "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
      "function_name": "ZSTD_initCStream",
      "calling_convention": "stdcall",
      "charset": "ansi"
    },
    "return_type": "size_t",
    "parameters": [
      {
        "name": "zcs",
        "type": "ZSTD_CStream*",
        "required": true,
        "description": "ZSTD_CStream*"
      },
      {
        "name": "compressionLevel",
        "type": "int",
        "required": true,
        "description": "int"
      }
    ]
  },
  {
    "name": "ZSTD_initCStream_advanced",
    "tool_id": "dll_ZSTD_initCStream_advanced_150",
    "kind": "export",
    "ordinal": 150,
    "rva": "00013CE0",
    "confidence": "guaranteed",
    "confidence_factors": {
      "has_signature": true,
      "has_documentation": false,
      "has_parameters": true,
      "has_return_type": true,
      "is_forwarded": true,
      "is_ordinal_only": false
    },
    "signature": {
      "return_type": "size_t",
      "parameters": "ZSTD_CStream* zcs, const void* dict, size_t dictSize, ZSTD_parameters params, unsigned long long pledgedSrcSize",
      "calling_convention": null,
      "full_prototype": "size_t ZSTD_initCStream_advanced(ZSTD_CStream* zcs, const void* dict, size_t dictSize, ZSTD_parameters params, unsigned long long pledgedSrcSize)"
    },
    "documentation": {
      "summary": "",
      "description": "",
      "source_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "source_line": 2677
    },
    "evidence": {
      "discovered_by": "export_analyzer",
      "header_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "forwarded_to": null,
      "demangled_name": null
    },
    "mcp": {
      "version": "1.0",
      "input_schema": {
        "type": "object",
        "properties": {
          "zcs": {
            "type": "string",
            "description": "ZSTD_CStream*"
          },
          "dict": {
            "type": "string",
            "description": "void*"
          },
          "dictSize": {
            "type": "integer",
            "description": "size_t"
          },
          "params": {
            "type": "string",
            "description": "ZSTD_parameters"
          },
          "pledgedSrcSize": {
            "type": "integer",
            "description": "unsigned long long"
          }
        },
        "required": [
          "zcs",
          "dict",
          "dictSize",
          "params",
          "pledgedSrcSize"
        ]
      },
      "execution": {
        "method": "dll_import",
        "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
        "function_name": "ZSTD_initCStream_advanced",
        "calling_convention": "stdcall",
        "charset": "ansi"
      }
    },
    "metadata": {
      "is_signed": false,
      "publisher": null
    },
    "execution": {
      "method": "dll_import",
      "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
      "function_name": "ZSTD_initCStream_advanced",
      "calling_convention": "stdcall",
      "charset": "ansi"
    },
    "return_type": "size_t",
    "parameters": [
      {
        "name": "zcs",
        "type": "ZSTD_CStream*",
        "required": true,
        "description": "ZSTD_CStream*"
      },
      {
        "name": "dict",
        "type": "const void*",
        "required": true,
        "description": "const void*"
      },
      {
        "name": "dictSize",
        "type": "size_t",
        "required": true,
        "description": "size_t"
      },
      {
        "name": "params",
        "type": "ZSTD_parameters",
        "required": true,
        "description": "ZSTD_parameters"
      },
      {
        "name": "pledgedSrcSize",
        "type": "unsigned long long",
        "required": true,
        "description": "unsigned long long"
      }
    ]
  },
  {
    "name": "ZSTD_initCStream_srcSize",
    "tool_id": "dll_ZSTD_initCStream_srcSize_151",
    "kind": "export",
    "ordinal": 151,
    "rva": "00013E40",
    "confidence": "guaranteed",
    "confidence_factors": {
      "has_signature": true,
      "has_documentation": false,
      "has_parameters": true,
      "has_return_type": true,
      "is_forwarded": true,
      "is_ordinal_only": false
    },
    "signature": {
      "return_type": "size_t",
      "parameters": "ZSTD_CStream* zcs, int compressionLevel, unsigned long long pledgedSrcSize",
      "calling_convention": null,
      "full_prototype": "size_t ZSTD_initCStream_srcSize(ZSTD_CStream* zcs, int compressionLevel, unsigned long long pledgedSrcSize)"
    },
    "documentation": {
      "summary": "",
      "description": "",
      "source_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "source_line": 2641
    },
    "evidence": {
      "discovered_by": "export_analyzer",
      "header_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "forwarded_to": null,
      "demangled_name": null
    },
    "mcp": {
      "version": "1.0",
      "input_schema": {
        "type": "object",
        "properties": {
          "zcs": {
            "type": "string",
            "description": "ZSTD_CStream*"
          },
          "compressionLevel": {
            "type": "integer",
            "description": "int"
          },
          "pledgedSrcSize": {
            "type": "integer",
            "description": "unsigned long long"
          }
        },
        "required": [
          "zcs",
          "compressionLevel",
          "pledgedSrcSize"
        ]
      },
      "execution": {
        "method": "dll_import",
        "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
        "function_name": "ZSTD_initCStream_srcSize",
        "calling_convention": "stdcall",
        "charset": "ansi"
      }
    },
    "metadata": {
      "is_signed": false,
      "publisher": null
    },
    "execution": {
      "method": "dll_import",
      "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
      "function_name": "ZSTD_initCStream_srcSize",
      "calling_convention": "stdcall",
      "charset": "ansi"
    },
    "return_type": "size_t",
    "parameters": [
      {
        "name": "zcs",
        "type": "ZSTD_CStream*",
        "required": true,
        "description": "ZSTD_CStream*"
      },
      {
        "name": "compressionLevel",
        "type": "int",
        "required": true,
        "description": "int"
      },
      {
        "name": "pledgedSrcSize",
        "type": "unsigned long long",
        "required": true,
        "description": "unsigned long long"
      }
    ]
  },
  {
    "name": "ZSTD_initCStream_usingCDict",
    "tool_id": "dll_ZSTD_initCStream_usingCDict_152",
    "kind": "export",
    "ordinal": 152,
    "rva": "00013F00",
    "confidence": "guaranteed",
    "confidence_factors": {
      "has_signature": true,
      "has_documentation": false,
      "has_parameters": true,
      "has_return_type": true,
      "is_forwarded": true,
      "is_ordinal_only": false
    },
    "signature": {
      "return_type": "size_t",
      "parameters": "ZSTD_CStream* zcs, const ZSTD_CDict* cdict",
      "calling_convention": null,
      "full_prototype": "size_t ZSTD_initCStream_usingCDict(ZSTD_CStream* zcs, const ZSTD_CDict* cdict)"
    },
    "documentation": {
      "summary": "",
      "description": "",
      "source_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "source_line": 2692
    },
    "evidence": {
      "discovered_by": "export_analyzer",
      "header_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "forwarded_to": null,
      "demangled_name": null
    },
    "mcp": {
      "version": "1.0",
      "input_schema": {
        "type": "object",
        "properties": {
          "zcs": {
            "type": "string",
            "description": "ZSTD_CStream*"
          },
          "cdict": {
            "type": "string",
            "description": "ZSTD_CDict*"
          }
        },
        "required": [
          "zcs",
          "cdict"
        ]
      },
      "execution": {
        "method": "dll_import",
        "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
        "function_name": "ZSTD_initCStream_usingCDict",
        "calling_convention": "stdcall",
        "charset": "ansi"
      }
    },
    "metadata": {
      "is_signed": false,
      "publisher": null
    },
    "execution": {
      "method": "dll_import",
      "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
      "function_name": "ZSTD_initCStream_usingCDict",
      "calling_convention": "stdcall",
      "charset": "ansi"
    },
    "return_type": "size_t",
    "parameters": [
      {
        "name": "zcs",
        "type": "ZSTD_CStream*",
        "required": true,
        "description": "ZSTD_CStream*"
      },
      {
        "name": "cdict",
        "type": "const ZSTD_CDict*",
        "required": true,
        "description": "const ZSTD_CDict*"
      }
    ]
  },
  {
    "name": "ZSTD_initCStream_usingCDict_advanced",
    "tool_id": "dll_ZSTD_initCStream_usingCDict_advanced_153",
    "kind": "export",
    "ordinal": 153,
    "rva": "00013F60",
    "confidence": "guaranteed",
    "confidence_factors": {
      "has_signature": true,
      "has_documentation": false,
      "has_parameters": true,
      "has_return_type": true,
      "is_forwarded": true,
      "is_ordinal_only": false
    },
    "signature": {
      "return_type": "size_t",
      "parameters": "ZSTD_CStream* zcs, const ZSTD_CDict* cdict, ZSTD_frameParameters fParams, unsigned long long pledgedSrcSize",
      "calling_convention": null,
      "full_prototype": "size_t ZSTD_initCStream_usingCDict_advanced(ZSTD_CStream* zcs, const ZSTD_CDict* cdict, ZSTD_frameParameters fParams, unsigned long long pledgedSrcSize)"
    },
    "documentation": {
      "summary": "",
      "description": "",
      "source_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "source_line": 2708
    },
    "evidence": {
      "discovered_by": "export_analyzer",
      "header_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "forwarded_to": null,
      "demangled_name": null
    },
    "mcp": {
      "version": "1.0",
      "input_schema": {
        "type": "object",
        "properties": {
          "zcs": {
            "type": "string",
            "description": "ZSTD_CStream*"
          },
          "cdict": {
            "type": "string",
            "description": "ZSTD_CDict*"
          },
          "fParams": {
            "type": "string",
            "description": "ZSTD_frameParameters"
          },
          "pledgedSrcSize": {
            "type": "integer",
            "description": "unsigned long long"
          }
        },
        "required": [
          "zcs",
          "cdict",
          "fParams",
          "pledgedSrcSize"
        ]
      },
      "execution": {
        "method": "dll_import",
        "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
        "function_name": "ZSTD_initCStream_usingCDict_advanced",
        "calling_convention": "stdcall",
        "charset": "ansi"
      }
    },
    "metadata": {
      "is_signed": false,
      "publisher": null
    },
    "execution": {
      "method": "dll_import",
      "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
      "function_name": "ZSTD_initCStream_usingCDict_advanced",
      "calling_convention": "stdcall",
      "charset": "ansi"
    },
    "return_type": "size_t",
    "parameters": [
      {
        "name": "zcs",
        "type": "ZSTD_CStream*",
        "required": true,
        "description": "ZSTD_CStream*"
      },
      {
        "name": "cdict",
        "type": "const ZSTD_CDict*",
        "required": true,
        "description": "const ZSTD_CDict*"
      },
      {
        "name": "fParams",
        "type": "ZSTD_frameParameters",
        "required": true,
        "description": "ZSTD_frameParameters"
      },
      {
        "name": "pledgedSrcSize",
        "type": "unsigned long long",
        "required": true,
        "description": "unsigned long long"
      }
    ]
  },
  {
    "name": "ZSTD_initCStream_usingDict",
    "tool_id": "dll_ZSTD_initCStream_usingDict_154",
    "kind": "export",
    "ordinal": 154,
    "rva": "00013FF0",
    "confidence": "guaranteed",
    "confidence_factors": {
      "has_signature": true,
      "has_documentation": false,
      "has_parameters": true,
      "has_return_type": true,
      "is_forwarded": true,
      "is_ordinal_only": false
    },
    "signature": {
      "return_type": "size_t",
      "parameters": "ZSTD_CStream* zcs, const void* dict, size_t dictSize, int compressionLevel",
      "calling_convention": null,
      "full_prototype": "size_t ZSTD_initCStream_usingDict(ZSTD_CStream* zcs, const void* dict, size_t dictSize, int compressionLevel)"
    },
    "documentation": {
      "summary": "",
      "description": "",
      "source_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "source_line": 2659
    },
    "evidence": {
      "discovered_by": "export_analyzer",
      "header_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "forwarded_to": null,
      "demangled_name": null
    },
    "mcp": {
      "version": "1.0",
      "input_schema": {
        "type": "object",
        "properties": {
          "zcs": {
            "type": "string",
            "description": "ZSTD_CStream*"
          },
          "dict": {
            "type": "string",
            "description": "void*"
          },
          "dictSize": {
            "type": "integer",
            "description": "size_t"
          },
          "compressionLevel": {
            "type": "integer",
            "description": "int"
          }
        },
        "required": [
          "zcs",
          "dict",
          "dictSize",
          "compressionLevel"
        ]
      },
      "execution": {
        "method": "dll_import",
        "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
        "function_name": "ZSTD_initCStream_usingDict",
        "calling_convention": "stdcall",
        "charset": "ansi"
      }
    },
    "metadata": {
      "is_signed": false,
      "publisher": null
    },
    "execution": {
      "method": "dll_import",
      "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
      "function_name": "ZSTD_initCStream_usingDict",
      "calling_convention": "stdcall",
      "charset": "ansi"
    },
    "return_type": "size_t",
    "parameters": [
      {
        "name": "zcs",
        "type": "ZSTD_CStream*",
        "required": true,
        "description": "ZSTD_CStream*"
      },
      {
        "name": "dict",
        "type": "const void*",
        "required": true,
        "description": "const void*"
      },
      {
        "name": "dictSize",
        "type": "size_t",
        "required": true,
        "description": "size_t"
      },
      {
        "name": "compressionLevel",
        "type": "int",
        "required": true,
        "description": "int"
      }
    ]
  },
  {
    "name": "ZSTD_initDStream",
    "tool_id": "dll_ZSTD_initDStream_155",
    "kind": "export",
    "ordinal": 155,
    "rva": "00068A10",
    "confidence": "guaranteed",
    "confidence_factors": {
      "has_signature": true,
      "has_documentation": true,
      "has_parameters": true,
      "has_return_type": true,
      "is_forwarded": true,
      "is_ordinal_only": false
    },
    "signature": {
      "return_type": "size_t",
      "parameters": "ZSTD_DStream* zds",
      "calling_convention": null,
      "full_prototype": "ZSTDLIB_API size_t ZSTD_initDStream(ZSTD_DStream* zds)"
    },
    "documentation": {
      "summary": "! ZSTD_initDStream() :\nInitialize/reset DStream state for new decompression operation.\nCall before new decompression operation using same DStream.\n\nNote : This function is redundant with the advanced API and equivalent to:\n    ZSTD_DCtx_reset(zds, ZSTD_reset_session_only);\n    ZSTD_DCtx_refDDict(zds, NULL);",
      "description": "! ZSTD_initDStream() :\nInitialize/reset DStream state for new decompression operation.\nCall before new decompression operation using same DStream.\n\nNote : This function is redundant with the advanced API and equivalent to:\n    ZSTD_DCtx_reset(zds, ZSTD_reset_session_only);\n    ZSTD_DCtx_refDDict(zds, NULL);",
      "source_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "source_line": 924
    },
    "evidence": {
      "discovered_by": "export_analyzer",
      "header_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "forwarded_to": null,
      "demangled_name": null
    },
    "mcp": {
      "version": "1.0",
      "input_schema": {
        "type": "object",
        "properties": {
          "zds": {
            "type": "string",
            "description": "ZSTD_DStream*"
          }
        },
        "required": [
          "zds"
        ]
      },
      "execution": {
        "method": "dll_import",
        "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
        "function_name": "ZSTD_initDStream",
        "calling_convention": "stdcall",
        "charset": "ansi"
      }
    },
    "metadata": {
      "is_signed": false,
      "publisher": null
    },
    "execution": {
      "method": "dll_import",
      "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
      "function_name": "ZSTD_initDStream",
      "calling_convention": "stdcall",
      "charset": "ansi"
    },
    "return_type": "size_t",
    "parameters": [
      {
        "name": "zds",
        "type": "ZSTD_DStream*",
        "required": true,
        "description": "ZSTD_DStream*"
      }
    ]
  },
  {
    "name": "ZSTD_initDStream_usingDDict",
    "tool_id": "dll_ZSTD_initDStream_usingDDict_156",
    "kind": "export",
    "ordinal": 156,
    "rva": "00068A90",
    "confidence": "guaranteed",
    "confidence_factors": {
      "has_signature": true,
      "has_documentation": false,
      "has_parameters": true,
      "has_return_type": true,
      "is_forwarded": true,
      "is_ordinal_only": false
    },
    "signature": {
      "return_type": "size_t",
      "parameters": "ZSTD_DStream* zds, const ZSTD_DDict* ddict",
      "calling_convention": null,
      "full_prototype": "ZSTDLIB_STATIC_API size_t ZSTD_initDStream_usingDDict(ZSTD_DStream* zds, const ZSTD_DDict* ddict)"
    },
    "documentation": {
      "summary": "",
      "description": "",
      "source_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "source_line": 2791
    },
    "evidence": {
      "discovered_by": "export_analyzer",
      "header_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "forwarded_to": null,
      "demangled_name": null
    },
    "mcp": {
      "version": "1.0",
      "input_schema": {
        "type": "object",
        "properties": {
          "zds": {
            "type": "string",
            "description": "ZSTD_DStream*"
          },
          "ddict": {
            "type": "string",
            "description": "ZSTD_DDict*"
          }
        },
        "required": [
          "zds",
          "ddict"
        ]
      },
      "execution": {
        "method": "dll_import",
        "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
        "function_name": "ZSTD_initDStream_usingDDict",
        "calling_convention": "stdcall",
        "charset": "ansi"
      }
    },
    "metadata": {
      "is_signed": false,
      "publisher": null
    },
    "execution": {
      "method": "dll_import",
      "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
      "function_name": "ZSTD_initDStream_usingDDict",
      "calling_convention": "stdcall",
      "charset": "ansi"
    },
    "return_type": "size_t",
    "parameters": [
      {
        "name": "zds",
        "type": "ZSTD_DStream*",
        "required": true,
        "description": "ZSTD_DStream*"
      },
      {
        "name": "ddict",
        "type": "const ZSTD_DDict*",
        "required": true,
        "description": "const ZSTD_DDict*"
      }
    ]
  },
  {
    "name": "ZSTD_initDStream_usingDict",
    "tool_id": "dll_ZSTD_initDStream_usingDict_157",
    "kind": "export",
    "ordinal": 157,
    "rva": "00068AF0",
    "confidence": "guaranteed",
    "confidence_factors": {
      "has_signature": true,
      "has_documentation": false,
      "has_parameters": true,
      "has_return_type": true,
      "is_forwarded": true,
      "is_ordinal_only": false
    },
    "signature": {
      "return_type": "size_t",
      "parameters": "ZSTD_DStream* zds, const void* dict, size_t dictSize",
      "calling_convention": null,
      "full_prototype": "ZSTDLIB_STATIC_API size_t ZSTD_initDStream_usingDict(ZSTD_DStream* zds, const void* dict, size_t dictSize)"
    },
    "documentation": {
      "summary": "",
      "description": "",
      "source_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "source_line": 2780
    },
    "evidence": {
      "discovered_by": "export_analyzer",
      "header_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "forwarded_to": null,
      "demangled_name": null
    },
    "mcp": {
      "version": "1.0",
      "input_schema": {
        "type": "object",
        "properties": {
          "zds": {
            "type": "string",
            "description": "ZSTD_DStream*"
          },
          "dict": {
            "type": "string",
            "description": "void*"
          },
          "dictSize": {
            "type": "integer",
            "description": "size_t"
          }
        },
        "required": [
          "zds",
          "dict",
          "dictSize"
        ]
      },
      "execution": {
        "method": "dll_import",
        "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
        "function_name": "ZSTD_initDStream_usingDict",
        "calling_convention": "stdcall",
        "charset": "ansi"
      }
    },
    "metadata": {
      "is_signed": false,
      "publisher": null
    },
    "execution": {
      "method": "dll_import",
      "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
      "function_name": "ZSTD_initDStream_usingDict",
      "calling_convention": "stdcall",
      "charset": "ansi"
    },
    "return_type": "size_t",
    "parameters": [
      {
        "name": "zds",
        "type": "ZSTD_DStream*",
        "required": true,
        "description": "ZSTD_DStream*"
      },
      {
        "name": "dict",
        "type": "const void*",
        "required": true,
        "description": "const void*"
      },
      {
        "name": "dictSize",
        "type": "size_t",
        "required": true,
        "description": "size_t"
      }
    ]
  },
  {
    "name": "ZSTD_initStaticCCtx",
    "tool_id": "dll_ZSTD_initStaticCCtx_158",
    "kind": "export",
    "ordinal": 158,
    "rva": "00014080",
    "confidence": "guaranteed",
    "confidence_factors": {
      "has_signature": true,
      "has_documentation": true,
      "has_parameters": true,
      "has_return_type": true,
      "is_forwarded": true,
      "is_ordinal_only": false
    },
    "signature": {
      "return_type": "ZSTD_CCtx*",
      "parameters": "void* workspace, size_t workspaceSize",
      "calling_convention": null,
      "full_prototype": "ZSTDLIB_STATIC_API ZSTD_CCtx* ZSTD_initStaticCCtx(void* workspace, size_t workspaceSize)"
    },
    "documentation": {
      "summary": "! ZSTD_initStatic*() :\n Initialize an object using a pre-allocated fixed-size buffer.\n workspace: The memory area to emplace the object into.\n            Provided pointer *must be 8-bytes aligned*.\n            Buffer must outlive object.\n workspaceSize: Use ZSTD_estimate*Size() to determine\n                how large workspace must be to support target scenario.\n@return : pointer to object (same address as workspace, just different type),\n          or NULL if error (size too small, incorrect alignment, etc.)\n Note : zstd will never resize nor malloc() when using a static buffer.\n        If the object requires more memory than available,\n        zstd will just error out (typically ZSTD_error_memory_allocation).\n Note 2 : there is no corresponding \"free\" function.\n          Since workspace is allocated externally, it must be freed externally too.\n Note 3 : cParams : use ZSTD_getCParams() to convert a compression level\n          into its associated cParams.\n Limitation 1 : currently not compatible with internal dictionary creation, triggered by\n                ZSTD_CCtx_loadDictionary(), ZSTD_initCStream_usingDict() or ZSTD_initDStream_usingDict().\n Limitation 2 : static cctx currently not compatible with multi-threading.\n Limitation 3 : static dctx is incompatible with legacy support.",
      "description": "! ZSTD_initStatic*() :\n Initialize an object using a pre-allocated fixed-size buffer.\n workspace: The memory area to emplace the object into.\n            Provided pointer *must be 8-bytes aligned*.\n            Buffer must outlive object.\n workspaceSize: Use ZSTD_estimate*Size() to determine\n                how large workspace must be to support target scenario.\n@return : pointer to object (same address as workspace, just different type),\n          or NULL if error (size too small, incorrect alignment, etc.)\n Note : zstd will never resize nor malloc() when using a static buffer.\n        If the object requires more memory than available,\n        zstd will just error out (typically ZSTD_error_memory_allocation).\n Note 2 : there is no corresponding \"free\" function.\n          Since workspace is allocated externally, it must be freed externally too.\n Note 3 : cParams : use ZSTD_getCParams() to convert a compression level\n          into its associated cParams.\n Limitation 1 : currently not compatible with internal dictionary creation, triggered by\n                ZSTD_CCtx_loadDictionary(), ZSTD_initCStream_usingDict() or ZSTD_initDStream_usingDict().\n Limitation 2 : static cctx currently not compatible with multi-threading.\n Limitation 3 : static dctx is incompatible with legacy support.",
      "source_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "source_line": 1843
    },
    "evidence": {
      "discovered_by": "export_analyzer",
      "header_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "forwarded_to": null,
      "demangled_name": null
    },
    "mcp": {
      "version": "1.0",
      "input_schema": {
        "type": "object",
        "properties": {
          "workspace": {
            "type": "string",
            "description": "void*"
          },
          "workspaceSize": {
            "type": "integer",
            "description": "size_t"
          }
        },
        "required": [
          "workspace",
          "workspaceSize"
        ]
      },
      "execution": {
        "method": "dll_import",
        "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
        "function_name": "ZSTD_initStaticCCtx",
        "calling_convention": "stdcall",
        "charset": "ansi"
      }
    },
    "metadata": {
      "is_signed": false,
      "publisher": null
    },
    "execution": {
      "method": "dll_import",
      "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
      "function_name": "ZSTD_initStaticCCtx",
      "calling_convention": "stdcall",
      "charset": "ansi"
    },
    "return_type": "ZSTD_CCtx*",
    "parameters": [
      {
        "name": "workspace",
        "type": "void*",
        "required": true,
        "description": "void*"
      },
      {
        "name": "workspaceSize",
        "type": "size_t",
        "required": true,
        "description": "size_t"
      }
    ]
  },
  {
    "name": "ZSTD_initStaticCDict",
    "tool_id": "dll_ZSTD_initStaticCDict_159",
    "kind": "export",
    "ordinal": 159,
    "rva": "00014350",
    "confidence": "guaranteed",
    "confidence_factors": {
      "has_signature": true,
      "has_documentation": false,
      "has_parameters": true,
      "has_return_type": true,
      "is_forwarded": true,
      "is_ordinal_only": false
    },
    "signature": {
      "return_type": "const ZSTD_CDict*",
      "parameters": "void* workspace, size_t workspaceSize, const void* dict, size_t dictSize, ZSTD_dictLoadMethod_e dictLoadMethod, ZSTD_dictContentType_e dictContentType, ZSTD_compressionParameters cParams",
      "calling_convention": null,
      "full_prototype": "ZSTDLIB_STATIC_API const ZSTD_CDict* ZSTD_initStaticCDict( void* workspace, size_t workspaceSize, const void* dict, size_t dictSize, ZSTD_dictLoadMethod_e dictLoadMethod, ZSTD_dictContentType_e dictContentType, ZSTD_compressionParameters cParams)"
    },
    "documentation": {
      "summary": "",
      "description": "",
      "source_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "source_line": 1849
    },
    "evidence": {
      "discovered_by": "export_analyzer",
      "header_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "forwarded_to": null,
      "demangled_name": null
    },
    "mcp": {
      "version": "1.0",
      "input_schema": {
        "type": "object",
        "properties": {
          "workspace": {
            "type": "string",
            "description": "void*"
          },
          "workspaceSize": {
            "type": "integer",
            "description": "size_t"
          },
          "dict": {
            "type": "string",
            "description": "void*"
          },
          "dictSize": {
            "type": "integer",
            "description": "size_t"
          },
          "dictLoadMethod": {
            "type": "string",
            "description": "ZSTD_dictLoadMethod_e"
          },
          "dictContentType": {
            "type": "string",
            "description": "ZSTD_dictContentType_e"
          },
          "cParams": {
            "type": "string",
            "description": "ZSTD_compressionParameters"
          }
        },
        "required": [
          "workspace",
          "workspaceSize",
          "dict",
          "dictSize",
          "dictLoadMethod",
          "dictContentType",
          "cParams"
        ]
      },
      "execution": {
        "method": "dll_import",
        "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
        "function_name": "ZSTD_initStaticCDict",
        "calling_convention": "stdcall",
        "charset": "ansi"
      }
    },
    "metadata": {
      "is_signed": false,
      "publisher": null
    },
    "execution": {
      "method": "dll_import",
      "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
      "function_name": "ZSTD_initStaticCDict",
      "calling_convention": "stdcall",
      "charset": "ansi"
    },
    "return_type": "const ZSTD_CDict*",
    "parameters": [
      {
        "name": "workspace",
        "type": "void*",
        "required": true,
        "description": "void*"
      },
      {
        "name": "workspaceSize",
        "type": "size_t",
        "required": true,
        "description": "size_t"
      },
      {
        "name": "dict",
        "type": "const void*",
        "required": true,
        "description": "const void*"
      },
      {
        "name": "dictSize",
        "type": "size_t",
        "required": true,
        "description": "size_t"
      },
      {
        "name": "dictLoadMethod",
        "type": "ZSTD_dictLoadMethod_e",
        "required": true,
        "description": "ZSTD_dictLoadMethod_e"
      },
      {
        "name": "dictContentType",
        "type": "ZSTD_dictContentType_e",
        "required": true,
        "description": "ZSTD_dictContentType_e"
      },
      {
        "name": "cParams",
        "type": "ZSTD_compressionParameters",
        "required": true,
        "description": "ZSTD_compressionParameters"
      }
    ]
  },
  {
    "name": "ZSTD_initStaticCStream",
    "tool_id": "dll_ZSTD_initStaticCStream_160",
    "kind": "export",
    "ordinal": 160,
    "rva": "000145C0",
    "confidence": "guaranteed",
    "confidence_factors": {
      "has_signature": true,
      "has_documentation": true,
      "has_parameters": true,
      "has_return_type": true,
      "is_forwarded": true,
      "is_ordinal_only": false
    },
    "signature": {
      "return_type": "ZSTD_CStream*",
      "parameters": "void* workspace, size_t workspaceSize",
      "calling_convention": null,
      "full_prototype": "ZSTDLIB_STATIC_API ZSTD_CStream* ZSTD_initStaticCStream(void* workspace, size_t workspaceSize)"
    },
    "documentation": {
      "summary": "*< same as ZSTD_initStaticCCtx()",
      "description": "*< same as ZSTD_initStaticCCtx()",
      "source_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "source_line": 1844
    },
    "evidence": {
      "discovered_by": "export_analyzer",
      "header_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "forwarded_to": null,
      "demangled_name": null
    },
    "mcp": {
      "version": "1.0",
      "input_schema": {
        "type": "object",
        "properties": {
          "workspace": {
            "type": "string",
            "description": "void*"
          },
          "workspaceSize": {
            "type": "integer",
            "description": "size_t"
          }
        },
        "required": [
          "workspace",
          "workspaceSize"
        ]
      },
      "execution": {
        "method": "dll_import",
        "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
        "function_name": "ZSTD_initStaticCStream",
        "calling_convention": "stdcall",
        "charset": "ansi"
      }
    },
    "metadata": {
      "is_signed": false,
      "publisher": null
    },
    "execution": {
      "method": "dll_import",
      "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
      "function_name": "ZSTD_initStaticCStream",
      "calling_convention": "stdcall",
      "charset": "ansi"
    },
    "return_type": "ZSTD_CStream*",
    "parameters": [
      {
        "name": "workspace",
        "type": "void*",
        "required": true,
        "description": "void*"
      },
      {
        "name": "workspaceSize",
        "type": "size_t",
        "required": true,
        "description": "size_t"
      }
    ]
  },
  {
    "name": "ZSTD_initStaticDCtx",
    "tool_id": "dll_ZSTD_initStaticDCtx_161",
    "kind": "export",
    "ordinal": 161,
    "rva": "00068B70",
    "confidence": "guaranteed",
    "confidence_factors": {
      "has_signature": true,
      "has_documentation": false,
      "has_parameters": true,
      "has_return_type": true,
      "is_forwarded": true,
      "is_ordinal_only": false
    },
    "signature": {
      "return_type": "ZSTD_DCtx*",
      "parameters": "void* workspace, size_t workspaceSize",
      "calling_convention": null,
      "full_prototype": "ZSTDLIB_STATIC_API ZSTD_DCtx* ZSTD_initStaticDCtx(void* workspace, size_t workspaceSize)"
    },
    "documentation": {
      "summary": "",
      "description": "",
      "source_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "source_line": 1846
    },
    "evidence": {
      "discovered_by": "export_analyzer",
      "header_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "forwarded_to": null,
      "demangled_name": null
    },
    "mcp": {
      "version": "1.0",
      "input_schema": {
        "type": "object",
        "properties": {
          "workspace": {
            "type": "string",
            "description": "void*"
          },
          "workspaceSize": {
            "type": "integer",
            "description": "size_t"
          }
        },
        "required": [
          "workspace",
          "workspaceSize"
        ]
      },
      "execution": {
        "method": "dll_import",
        "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
        "function_name": "ZSTD_initStaticDCtx",
        "calling_convention": "stdcall",
        "charset": "ansi"
      }
    },
    "metadata": {
      "is_signed": false,
      "publisher": null
    },
    "execution": {
      "method": "dll_import",
      "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
      "function_name": "ZSTD_initStaticDCtx",
      "calling_convention": "stdcall",
      "charset": "ansi"
    },
    "return_type": "ZSTD_DCtx*",
    "parameters": [
      {
        "name": "workspace",
        "type": "void*",
        "required": true,
        "description": "void*"
      },
      {
        "name": "workspaceSize",
        "type": "size_t",
        "required": true,
        "description": "size_t"
      }
    ]
  },
  {
    "name": "ZSTD_initStaticDDict",
    "tool_id": "dll_ZSTD_initStaticDDict_162",
    "kind": "export",
    "ordinal": 162,
    "rva": "000649C0",
    "confidence": "guaranteed",
    "confidence_factors": {
      "has_signature": true,
      "has_documentation": false,
      "has_parameters": true,
      "has_return_type": true,
      "is_forwarded": true,
      "is_ordinal_only": false
    },
    "signature": {
      "return_type": "const ZSTD_DDict*",
      "parameters": "void* workspace, size_t workspaceSize, const void* dict, size_t dictSize, ZSTD_dictLoadMethod_e dictLoadMethod, ZSTD_dictContentType_e dictContentType",
      "calling_convention": null,
      "full_prototype": "ZSTDLIB_STATIC_API const ZSTD_DDict* ZSTD_initStaticDDict( void* workspace, size_t workspaceSize, const void* dict, size_t dictSize, ZSTD_dictLoadMethod_e dictLoadMethod, ZSTD_dictContentType_e dictContentType)"
    },
    "documentation": {
      "summary": "",
      "description": "",
      "source_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "source_line": 1856
    },
    "evidence": {
      "discovered_by": "export_analyzer",
      "header_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "forwarded_to": null,
      "demangled_name": null
    },
    "mcp": {
      "version": "1.0",
      "input_schema": {
        "type": "object",
        "properties": {
          "workspace": {
            "type": "string",
            "description": "void*"
          },
          "workspaceSize": {
            "type": "integer",
            "description": "size_t"
          },
          "dict": {
            "type": "string",
            "description": "void*"
          },
          "dictSize": {
            "type": "integer",
            "description": "size_t"
          },
          "dictLoadMethod": {
            "type": "string",
            "description": "ZSTD_dictLoadMethod_e"
          },
          "dictContentType": {
            "type": "string",
            "description": "ZSTD_dictContentType_e"
          }
        },
        "required": [
          "workspace",
          "workspaceSize",
          "dict",
          "dictSize",
          "dictLoadMethod",
          "dictContentType"
        ]
      },
      "execution": {
        "method": "dll_import",
        "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
        "function_name": "ZSTD_initStaticDDict",
        "calling_convention": "stdcall",
        "charset": "ansi"
      }
    },
    "metadata": {
      "is_signed": false,
      "publisher": null
    },
    "execution": {
      "method": "dll_import",
      "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
      "function_name": "ZSTD_initStaticDDict",
      "calling_convention": "stdcall",
      "charset": "ansi"
    },
    "return_type": "const ZSTD_DDict*",
    "parameters": [
      {
        "name": "workspace",
        "type": "void*",
        "required": true,
        "description": "void*"
      },
      {
        "name": "workspaceSize",
        "type": "size_t",
        "required": true,
        "description": "size_t"
      },
      {
        "name": "dict",
        "type": "const void*",
        "required": true,
        "description": "const void*"
      },
      {
        "name": "dictSize",
        "type": "size_t",
        "required": true,
        "description": "size_t"
      },
      {
        "name": "dictLoadMethod",
        "type": "ZSTD_dictLoadMethod_e",
        "required": true,
        "description": "ZSTD_dictLoadMethod_e"
      },
      {
        "name": "dictContentType",
        "type": "ZSTD_dictContentType_e",
        "required": true,
        "description": "ZSTD_dictContentType_e"
      }
    ]
  },
  {
    "name": "ZSTD_initStaticDStream",
    "tool_id": "dll_ZSTD_initStaticDStream_163",
    "kind": "export",
    "ordinal": 163,
    "rva": "00068C20",
    "confidence": "guaranteed",
    "confidence_factors": {
      "has_signature": true,
      "has_documentation": true,
      "has_parameters": true,
      "has_return_type": true,
      "is_forwarded": true,
      "is_ordinal_only": false
    },
    "signature": {
      "return_type": "ZSTD_DStream*",
      "parameters": "void* workspace, size_t workspaceSize",
      "calling_convention": null,
      "full_prototype": "ZSTDLIB_STATIC_API ZSTD_DStream* ZSTD_initStaticDStream(void* workspace, size_t workspaceSize)"
    },
    "documentation": {
      "summary": "*< same as ZSTD_initStaticDCtx()",
      "description": "*< same as ZSTD_initStaticDCtx()",
      "source_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "source_line": 1847
    },
    "evidence": {
      "discovered_by": "export_analyzer",
      "header_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "forwarded_to": null,
      "demangled_name": null
    },
    "mcp": {
      "version": "1.0",
      "input_schema": {
        "type": "object",
        "properties": {
          "workspace": {
            "type": "string",
            "description": "void*"
          },
          "workspaceSize": {
            "type": "integer",
            "description": "size_t"
          }
        },
        "required": [
          "workspace",
          "workspaceSize"
        ]
      },
      "execution": {
        "method": "dll_import",
        "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
        "function_name": "ZSTD_initStaticDStream",
        "calling_convention": "stdcall",
        "charset": "ansi"
      }
    },
    "metadata": {
      "is_signed": false,
      "publisher": null
    },
    "execution": {
      "method": "dll_import",
      "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
      "function_name": "ZSTD_initStaticDStream",
      "calling_convention": "stdcall",
      "charset": "ansi"
    },
    "return_type": "ZSTD_DStream*",
    "parameters": [
      {
        "name": "workspace",
        "type": "void*",
        "required": true,
        "description": "void*"
      },
      {
        "name": "workspaceSize",
        "type": "size_t",
        "required": true,
        "description": "size_t"
      }
    ]
  },
  {
    "name": "ZSTD_insertBlock",
    "tool_id": "dll_ZSTD_insertBlock_164",
    "kind": "export",
    "ordinal": 164,
    "rva": "00068C30",
    "confidence": "medium",
    "confidence_factors": {
      "has_signature": false,
      "has_documentation": false,
      "has_parameters": false,
      "has_return_type": false,
      "is_forwarded": true,
      "is_ordinal_only": false
    },
    "signature": {
      "return_type": null,
      "parameters": "",
      "calling_convention": null,
      "full_prototype": "void ZSTD_insertBlock()"
    },
    "documentation": {
      "summary": null,
      "description": null,
      "source_file": null,
      "source_line": null
    },
    "evidence": {
      "discovered_by": "export_analyzer",
      "header_file": null,
      "forwarded_to": null,
      "demangled_name": null
    },
    "mcp": {
      "version": "1.0",
      "input_schema": {
        "type": "object",
        "properties": {},
        "required": []
      },
      "execution": {
        "method": "dll_import",
        "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
        "function_name": "ZSTD_insertBlock",
        "calling_convention": "stdcall",
        "charset": "ansi"
      }
    },
    "metadata": {
      "is_signed": false,
      "publisher": null
    },
    "execution": {
      "method": "dll_import",
      "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
      "function_name": "ZSTD_insertBlock",
      "calling_convention": "stdcall",
      "charset": "ansi"
    }
  },
  {
    "name": "ZSTD_isError",
    "tool_id": "dll_ZSTD_isError_165",
    "kind": "export",
    "ordinal": 165,
    "rva": "00003610",
    "confidence": "guaranteed",
    "confidence_factors": {
      "has_signature": true,
      "has_documentation": true,
      "has_parameters": true,
      "has_return_type": true,
      "is_forwarded": true,
      "is_ordinal_only": false
    },
    "signature": {
      "return_type": "unsigned",
      "parameters": "size_t result",
      "calling_convention": null,
      "full_prototype": "ZSTDLIB_API unsigned ZSTD_isError(size_t result)"
    },
    "documentation": {
      "summary": "!< tells if a `size_t` function result is an error code",
      "description": "!< tells if a `size_t` function result is an error code",
      "source_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "source_line": 259
    },
    "evidence": {
      "discovered_by": "export_analyzer",
      "header_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "forwarded_to": null,
      "demangled_name": null
    },
    "mcp": {
      "version": "1.0",
      "input_schema": {
        "type": "object",
        "properties": {
          "result": {
            "type": "integer",
            "description": "size_t"
          }
        },
        "required": [
          "result"
        ]
      },
      "execution": {
        "method": "dll_import",
        "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
        "function_name": "ZSTD_isError",
        "calling_convention": "stdcall",
        "charset": "ansi"
      }
    },
    "metadata": {
      "is_signed": false,
      "publisher": null
    },
    "execution": {
      "method": "dll_import",
      "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
      "function_name": "ZSTD_isError",
      "calling_convention": "stdcall",
      "charset": "ansi"
    },
    "return_type": "unsigned",
    "parameters": [
      {
        "name": "result",
        "type": "size_t",
        "required": true,
        "description": "size_t"
      }
    ]
  },
  {
    "name": "ZSTD_isFrame",
    "tool_id": "dll_ZSTD_isFrame_166",
    "kind": "export",
    "ordinal": 166,
    "rva": "00068C70",
    "confidence": "guaranteed",
    "confidence_factors": {
      "has_signature": true,
      "has_documentation": true,
      "has_parameters": true,
      "has_return_type": true,
      "is_forwarded": true,
      "is_ordinal_only": false
    },
    "signature": {
      "return_type": "unsigned",
      "parameters": "const void* buffer, size_t size",
      "calling_convention": null,
      "full_prototype": "ZSTDLIB_STATIC_API unsigned ZSTD_isFrame(const void* buffer, size_t size)"
    },
    "documentation": {
      "summary": "! ZSTD_isFrame() :\n Tells if the content of `buffer` starts with a valid Frame Identifier.\n Note : Frame Identifier is 4 bytes. If `size < 4`, @return will always be 0.\n Note 2 : Legacy Frame Identifiers are considered valid only if Legacy Support is enabled.\n Note 3 : Skippable Frame Identifiers are considered valid.",
      "description": "! ZSTD_isFrame() :\n Tells if the content of `buffer` starts with a valid Frame Identifier.\n Note : Frame Identifier is 4 bytes. If `size < 4`, @return will always be 0.\n Note 2 : Legacy Frame Identifiers are considered valid only if Legacy Support is enabled.\n Note 3 : Skippable Frame Identifiers are considered valid.",
      "source_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "source_line": 2453
    },
    "evidence": {
      "discovered_by": "export_analyzer",
      "header_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "forwarded_to": null,
      "demangled_name": null
    },
    "mcp": {
      "version": "1.0",
      "input_schema": {
        "type": "object",
        "properties": {
          "buffer": {
            "type": "string",
            "description": "void*"
          },
          "size": {
            "type": "integer",
            "description": "size_t"
          }
        },
        "required": [
          "buffer",
          "size"
        ]
      },
      "execution": {
        "method": "dll_import",
        "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
        "function_name": "ZSTD_isFrame",
        "calling_convention": "stdcall",
        "charset": "ansi"
      }
    },
    "metadata": {
      "is_signed": false,
      "publisher": null
    },
    "execution": {
      "method": "dll_import",
      "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
      "function_name": "ZSTD_isFrame",
      "calling_convention": "stdcall",
      "charset": "ansi"
    },
    "return_type": "unsigned",
    "parameters": [
      {
        "name": "buffer",
        "type": "const void*",
        "required": true,
        "description": "const void*"
      },
      {
        "name": "size",
        "type": "size_t",
        "required": true,
        "description": "size_t"
      }
    ]
  },
  {
    "name": "ZSTD_isSkippableFrame",
    "tool_id": "dll_ZSTD_isSkippableFrame_167",
    "kind": "export",
    "ordinal": 167,
    "rva": "00068CB0",
    "confidence": "guaranteed",
    "confidence_factors": {
      "has_signature": true,
      "has_documentation": true,
      "has_parameters": true,
      "has_return_type": true,
      "is_forwarded": true,
      "is_ordinal_only": false
    },
    "signature": {
      "return_type": "unsigned",
      "parameters": "const void* buffer, size_t size",
      "calling_convention": null,
      "full_prototype": "ZSTDLIB_STATIC_API unsigned ZSTD_isSkippableFrame(const void* buffer, size_t size)"
    },
    "documentation": {
      "summary": "! ZSTD_isSkippableFrame() :\n Tells if the content of `buffer` starts with a valid Frame Identifier for a skippable frame.",
      "description": "! ZSTD_isSkippableFrame() :\n Tells if the content of `buffer` starts with a valid Frame Identifier for a skippable frame.",
      "source_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "source_line": 1747
    },
    "evidence": {
      "discovered_by": "export_analyzer",
      "header_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "forwarded_to": null,
      "demangled_name": null
    },
    "mcp": {
      "version": "1.0",
      "input_schema": {
        "type": "object",
        "properties": {
          "buffer": {
            "type": "string",
            "description": "void*"
          },
          "size": {
            "type": "integer",
            "description": "size_t"
          }
        },
        "required": [
          "buffer",
          "size"
        ]
      },
      "execution": {
        "method": "dll_import",
        "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
        "function_name": "ZSTD_isSkippableFrame",
        "calling_convention": "stdcall",
        "charset": "ansi"
      }
    },
    "metadata": {
      "is_signed": false,
      "publisher": null
    },
    "execution": {
      "method": "dll_import",
      "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
      "function_name": "ZSTD_isSkippableFrame",
      "calling_convention": "stdcall",
      "charset": "ansi"
    },
    "return_type": "unsigned",
    "parameters": [
      {
        "name": "buffer",
        "type": "const void*",
        "required": true,
        "description": "const void*"
      },
      {
        "name": "size",
        "type": "size_t",
        "required": true,
        "description": "size_t"
      }
    ]
  },
  {
    "name": "ZSTD_maxCLevel",
    "tool_id": "dll_ZSTD_maxCLevel_168",
    "kind": "export",
    "ordinal": 168,
    "rva": "00014E50",
    "confidence": "guaranteed",
    "confidence_factors": {
      "has_signature": true,
      "has_documentation": true,
      "has_parameters": true,
      "has_return_type": true,
      "is_forwarded": true,
      "is_ordinal_only": false
    },
    "signature": {
      "return_type": "int",
      "parameters": "void",
      "calling_convention": null,
      "full_prototype": "ZSTDLIB_API int ZSTD_maxCLevel(void)"
    },
    "documentation": {
      "summary": "!< maximum compression level available",
      "description": "!< maximum compression level available",
      "source_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "source_line": 263
    },
    "evidence": {
      "discovered_by": "export_analyzer",
      "header_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "forwarded_to": null,
      "demangled_name": null
    },
    "mcp": {
      "version": "1.0",
      "input_schema": {
        "type": "object",
        "properties": {},
        "required": []
      },
      "execution": {
        "method": "dll_import",
        "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
        "function_name": "ZSTD_maxCLevel",
        "calling_convention": "stdcall",
        "charset": "ansi"
      }
    },
    "metadata": {
      "is_signed": false,
      "publisher": null
    },
    "execution": {
      "method": "dll_import",
      "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
      "function_name": "ZSTD_maxCLevel",
      "calling_convention": "stdcall",
      "charset": "ansi"
    },
    "return_type": "int"
  },
  {
    "name": "ZSTD_mergeBlockDelimiters",
    "tool_id": "dll_ZSTD_mergeBlockDelimiters_169",
    "kind": "export",
    "ordinal": 169,
    "rva": "00014E60",
    "confidence": "guaranteed",
    "confidence_factors": {
      "has_signature": true,
      "has_documentation": true,
      "has_parameters": true,
      "has_return_type": true,
      "is_forwarded": true,
      "is_ordinal_only": false
    },
    "signature": {
      "return_type": "size_t",
      "parameters": "ZSTD_Sequence* sequences, size_t seqsSize",
      "calling_convention": null,
      "full_prototype": "ZSTDLIB_STATIC_API size_t ZSTD_mergeBlockDelimiters(ZSTD_Sequence* sequences, size_t seqsSize)"
    },
    "documentation": {
      "summary": "! ZSTD_mergeBlockDelimiters() :\nGiven an array of ZSTD_Sequence, remove all sequences that represent block delimiters/last literals\nby merging them into the literals of the next sequence.\n\nAs such, the final generated result has no explicit representation of block boundaries,\nand the final last literals segment is not represented in the sequences.\n\nThe output of this function can be fed into ZSTD_compressSequences() with CCtx\nsetting of ZSTD_c_blockDelimiters as ZSTD_sf_noBlockDelimiters\n@return : number of sequences left after merging",
      "description": "! ZSTD_mergeBlockDelimiters() :\nGiven an array of ZSTD_Sequence, remove all sequences that represent block delimiters/last literals\nby merging them into the literals of the next sequence.\n\nAs such, the final generated result has no explicit representation of block boundaries,\nand the final last literals segment is not represented in the sequences.\n\nThe output of this function can be fed into ZSTD_compressSequences() with CCtx\nsetting of ZSTD_c_blockDelimiters as ZSTD_sf_noBlockDelimiters\n@return : number of sequences left after merging",
      "source_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "source_line": 1641
    },
    "evidence": {
      "discovered_by": "export_analyzer",
      "header_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "forwarded_to": null,
      "demangled_name": null
    },
    "mcp": {
      "version": "1.0",
      "input_schema": {
        "type": "object",
        "properties": {
          "sequences": {
            "type": "string",
            "description": "ZSTD_Sequence*"
          },
          "seqsSize": {
            "type": "integer",
            "description": "size_t"
          }
        },
        "required": [
          "sequences",
          "seqsSize"
        ]
      },
      "execution": {
        "method": "dll_import",
        "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
        "function_name": "ZSTD_mergeBlockDelimiters",
        "calling_convention": "stdcall",
        "charset": "ansi"
      }
    },
    "metadata": {
      "is_signed": false,
      "publisher": null
    },
    "execution": {
      "method": "dll_import",
      "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
      "function_name": "ZSTD_mergeBlockDelimiters",
      "calling_convention": "stdcall",
      "charset": "ansi"
    },
    "return_type": "size_t",
    "parameters": [
      {
        "name": "sequences",
        "type": "ZSTD_Sequence*",
        "required": true,
        "description": "ZSTD_Sequence*"
      },
      {
        "name": "seqsSize",
        "type": "size_t",
        "required": true,
        "description": "size_t"
      }
    ]
  },
  {
    "name": "ZSTD_minCLevel",
    "tool_id": "dll_ZSTD_minCLevel_170",
    "kind": "export",
    "ordinal": 170,
    "rva": "00014EB0",
    "confidence": "guaranteed",
    "confidence_factors": {
      "has_signature": true,
      "has_documentation": true,
      "has_parameters": true,
      "has_return_type": true,
      "is_forwarded": true,
      "is_ordinal_only": false
    },
    "signature": {
      "return_type": "int",
      "parameters": "void",
      "calling_convention": null,
      "full_prototype": "ZSTDLIB_API int ZSTD_minCLevel(void)"
    },
    "documentation": {
      "summary": "!< minimum negative compression level allowed, requires v1.4.0+",
      "description": "!< minimum negative compression level allowed, requires v1.4.0+",
      "source_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "source_line": 262
    },
    "evidence": {
      "discovered_by": "export_analyzer",
      "header_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "forwarded_to": null,
      "demangled_name": null
    },
    "mcp": {
      "version": "1.0",
      "input_schema": {
        "type": "object",
        "properties": {},
        "required": []
      },
      "execution": {
        "method": "dll_import",
        "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
        "function_name": "ZSTD_minCLevel",
        "calling_convention": "stdcall",
        "charset": "ansi"
      }
    },
    "metadata": {
      "is_signed": false,
      "publisher": null
    },
    "execution": {
      "method": "dll_import",
      "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
      "function_name": "ZSTD_minCLevel",
      "calling_convention": "stdcall",
      "charset": "ansi"
    },
    "return_type": "int"
  },
  {
    "name": "ZSTD_nextInputType",
    "tool_id": "dll_ZSTD_nextInputType_171",
    "kind": "export",
    "ordinal": 171,
    "rva": "00068F70",
    "confidence": "guaranteed",
    "confidence_factors": {
      "has_signature": true,
      "has_documentation": false,
      "has_parameters": false,
      "has_return_type": false,
      "is_forwarded": true,
      "is_ordinal_only": false
    },
    "signature": {
      "return_type": "",
      "parameters": "",
      "calling_convention": null,
      "full_prototype": "ZSTDLIB_STATIC_API ZSTD_nextInputType_e ZSTD_nextInputType(ZSTD_DCtx* dctx)"
    },
    "documentation": {
      "summary": "",
      "description": "",
      "source_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "source_line": 3137
    },
    "evidence": {
      "discovered_by": "export_analyzer",
      "header_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "forwarded_to": null,
      "demangled_name": null
    },
    "mcp": {
      "version": "1.0",
      "input_schema": {
        "type": "object",
        "properties": {},
        "required": []
      },
      "execution": {
        "method": "dll_import",
        "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
        "function_name": "ZSTD_nextInputType",
        "calling_convention": "stdcall",
        "charset": "ansi"
      }
    },
    "metadata": {
      "is_signed": false,
      "publisher": null
    },
    "execution": {
      "method": "dll_import",
      "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
      "function_name": "ZSTD_nextInputType",
      "calling_convention": "stdcall",
      "charset": "ansi"
    }
  },
  {
    "name": "ZSTD_nextSrcSizeToDecompress",
    "tool_id": "dll_ZSTD_nextSrcSizeToDecompress_172",
    "kind": "export",
    "ordinal": 172,
    "rva": "00068FD0",
    "confidence": "guaranteed",
    "confidence_factors": {
      "has_signature": true,
      "has_documentation": false,
      "has_parameters": true,
      "has_return_type": true,
      "is_forwarded": true,
      "is_ordinal_only": false
    },
    "signature": {
      "return_type": "size_t",
      "parameters": "ZSTD_DCtx* dctx",
      "calling_convention": null,
      "full_prototype": "ZSTDLIB_STATIC_API size_t ZSTD_nextSrcSizeToDecompress(ZSTD_DCtx* dctx)"
    },
    "documentation": {
      "summary": "",
      "description": "",
      "source_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "source_line": 3130
    },
    "evidence": {
      "discovered_by": "export_analyzer",
      "header_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "forwarded_to": null,
      "demangled_name": null
    },
    "mcp": {
      "version": "1.0",
      "input_schema": {
        "type": "object",
        "properties": {
          "dctx": {
            "type": "string",
            "description": "ZSTD_DCtx*"
          }
        },
        "required": [
          "dctx"
        ]
      },
      "execution": {
        "method": "dll_import",
        "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
        "function_name": "ZSTD_nextSrcSizeToDecompress",
        "calling_convention": "stdcall",
        "charset": "ansi"
      }
    },
    "metadata": {
      "is_signed": false,
      "publisher": null
    },
    "execution": {
      "method": "dll_import",
      "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
      "function_name": "ZSTD_nextSrcSizeToDecompress",
      "calling_convention": "stdcall",
      "charset": "ansi"
    },
    "return_type": "size_t",
    "parameters": [
      {
        "name": "dctx",
        "type": "ZSTD_DCtx*",
        "required": true,
        "description": "ZSTD_DCtx*"
      }
    ]
  },
  {
    "name": "ZSTD_readSkippableFrame",
    "tool_id": "dll_ZSTD_readSkippableFrame_173",
    "kind": "export",
    "ordinal": 173,
    "rva": "00068FE0",
    "confidence": "guaranteed",
    "confidence_factors": {
      "has_signature": true,
      "has_documentation": true,
      "has_parameters": true,
      "has_return_type": true,
      "is_forwarded": true,
      "is_ordinal_only": false
    },
    "signature": {
      "return_type": "size_t",
      "parameters": "void* dst, size_t dstCapacity, unsigned* magicVariant, const void* src, size_t srcSize",
      "calling_convention": null,
      "full_prototype": "ZSTDLIB_STATIC_API size_t ZSTD_readSkippableFrame(void* dst, size_t dstCapacity, unsigned* magicVariant, const void* src, size_t srcSize)"
    },
    "documentation": {
      "summary": "! ZSTD_readSkippableFrame() :\nRetrieves the content of a zstd skippable frame starting at @src, and writes it to @dst buffer.\n\nThe parameter @magicVariant will receive the magicVariant that was supplied when the frame was written,\ni.e. magicNumber - ZSTD_MAGIC_SKIPPABLE_START.\nThis can be NULL if the caller is not interested in the magicVariant.\n\nReturns an error if destination buffer is not large enough, or if the frame is not skippable.\n\n@return : number of bytes written or a ZSTD error.",
      "description": "! ZSTD_readSkippableFrame() :\nRetrieves the content of a zstd skippable frame starting at @src, and writes it to @dst buffer.\n\nThe parameter @magicVariant will receive the magicVariant that was supplied when the frame was written,\ni.e. magicNumber - ZSTD_MAGIC_SKIPPABLE_START.\nThis can be NULL if the caller is not interested in the magicVariant.\n\nReturns an error if destination buffer is not large enough, or if the frame is not skippable.\n\n@return : number of bytes written or a ZSTD error.",
      "source_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "source_line": 1740
    },
    "evidence": {
      "discovered_by": "export_analyzer",
      "header_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "forwarded_to": null,
      "demangled_name": null
    },
    "mcp": {
      "version": "1.0",
      "input_schema": {
        "type": "object",
        "properties": {
          "dst": {
            "type": "string",
            "description": "void*"
          },
          "dstCapacity": {
            "type": "integer",
            "description": "size_t"
          },
          "magicVariant": {
            "type": "string",
            "description": "unsigned*"
          },
          "src": {
            "type": "string",
            "description": "void*"
          },
          "srcSize": {
            "type": "integer",
            "description": "size_t"
          }
        },
        "required": [
          "dst",
          "dstCapacity",
          "magicVariant",
          "src",
          "srcSize"
        ]
      },
      "execution": {
        "method": "dll_import",
        "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
        "function_name": "ZSTD_readSkippableFrame",
        "calling_convention": "stdcall",
        "charset": "ansi"
      }
    },
    "metadata": {
      "is_signed": false,
      "publisher": null
    },
    "execution": {
      "method": "dll_import",
      "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
      "function_name": "ZSTD_readSkippableFrame",
      "calling_convention": "stdcall",
      "charset": "ansi"
    },
    "return_type": "size_t",
    "parameters": [
      {
        "name": "dst",
        "type": "void*",
        "required": true,
        "description": "void*"
      },
      {
        "name": "dstCapacity",
        "type": "size_t",
        "required": true,
        "description": "size_t"
      },
      {
        "name": "magicVariant",
        "type": "unsigned*",
        "required": true,
        "description": "unsigned*"
      },
      {
        "name": "src",
        "type": "const void*",
        "required": true,
        "description": "const void*"
      },
      {
        "name": "srcSize",
        "type": "size_t",
        "required": true,
        "description": "size_t"
      }
    ]
  },
  {
    "name": "ZSTD_registerSequenceProducer",
    "tool_id": "dll_ZSTD_registerSequenceProducer_174",
    "kind": "export",
    "ordinal": 174,
    "rva": "00015400",
    "confidence": "guaranteed",
    "confidence_factors": {
      "has_signature": true,
      "has_documentation": true,
      "has_parameters": true,
      "has_return_type": true,
      "is_forwarded": true,
      "is_ordinal_only": false
    },
    "signature": {
      "return_type": "void",
      "parameters": "ZSTD_CCtx* cctx, void* sequenceProducerState, ZSTD_sequenceProducer_F sequenceProducer",
      "calling_convention": null,
      "full_prototype": "ZSTDLIB_STATIC_API void ZSTD_registerSequenceProducer( ZSTD_CCtx* cctx, void* sequenceProducerState, ZSTD_sequenceProducer_F sequenceProducer )"
    },
    "documentation": {
      "summary": "! ZSTD_registerSequenceProducer() :\nInstruct zstd to use a block-level external sequence producer function.\n\nThe sequenceProducerState must be initialized by the caller, and the caller is\nresponsible for managing its lifetime. This parameter is sticky across\ncompressions. It will remain set until the user explicitly resets compression\nparameters.\n\nSequence producer registration is considered to be an \"advanced parameter\",\npart of the \"advanced API\". This means it will only have an effect on compression\nAPIs which respect advanced parameters, such as compress2() and compressStream2().\nOlder compression APIs such as compressCCtx(), which predate the introduction of\n\"advanced parameters\", will ignore any external sequence producer setting.\n\nThe sequence producer can be \"cleared\" by registering a NULL function pointer. This\nremoves all limitations described above in the \"LIMITATIONS\" section of the API docs.\n\nThe user is strongly encouraged to read the full API documentation (above) before\ncalling this function.",
      "description": "! ZSTD_registerSequenceProducer() :\nInstruct zstd to use a block-level external sequence producer function.\n\nThe sequenceProducerState must be initialized by the caller, and the caller is\nresponsible for managing its lifetime. This parameter is sticky across\ncompressions. It will remain set until the user explicitly resets compression\nparameters.\n\nSequence producer registration is considered to be an \"advanced parameter\",\npart of the \"advanced API\". This means it will only have an effect on compression\nAPIs which respect advanced parameters, such as compress2() and compressStream2().\nOlder compression APIs such as compressCCtx(), which predate the introduction of\n\"advanced parameters\", will ignore any external sequence producer setting.\n\nThe sequence producer can be \"cleared\" by registering a NULL function pointer. This\nremoves all limitations described above in the \"LIMITATIONS\" section of the API docs.\n\nThe user is strongly encouraged to read the full API documentation (above) before\ncalling this function.",
      "source_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "source_line": 2958
    },
    "evidence": {
      "discovered_by": "export_analyzer",
      "header_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "forwarded_to": null,
      "demangled_name": null
    },
    "mcp": {
      "version": "1.0",
      "input_schema": {
        "type": "object",
        "properties": {
          "cctx": {
            "type": "string",
            "description": "ZSTD_CCtx*"
          },
          "sequenceProducerState": {
            "type": "string",
            "description": "void*"
          },
          "sequenceProducer": {
            "type": "string",
            "description": "ZSTD_sequenceProducer_F"
          }
        },
        "required": [
          "cctx",
          "sequenceProducerState",
          "sequenceProducer"
        ]
      },
      "execution": {
        "method": "dll_import",
        "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
        "function_name": "ZSTD_registerSequenceProducer",
        "calling_convention": "stdcall",
        "charset": "ansi"
      }
    },
    "metadata": {
      "is_signed": false,
      "publisher": null
    },
    "execution": {
      "method": "dll_import",
      "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
      "function_name": "ZSTD_registerSequenceProducer",
      "calling_convention": "stdcall",
      "charset": "ansi"
    },
    "return_type": "void",
    "parameters": [
      {
        "name": "cctx",
        "type": "ZSTD_CCtx*",
        "required": true,
        "description": "ZSTD_CCtx*"
      },
      {
        "name": "sequenceProducerState",
        "type": "void*",
        "required": true,
        "description": "void*"
      },
      {
        "name": "sequenceProducer",
        "type": "ZSTD_sequenceProducer_F",
        "required": true,
        "description": "ZSTD_sequenceProducer_F"
      }
    ]
  },
  {
    "name": "ZSTD_resetCStream",
    "tool_id": "dll_ZSTD_resetCStream_175",
    "kind": "export",
    "ordinal": 175,
    "rva": "00016040",
    "confidence": "guaranteed",
    "confidence_factors": {
      "has_signature": true,
      "has_documentation": false,
      "has_parameters": true,
      "has_return_type": true,
      "is_forwarded": true,
      "is_ordinal_only": false
    },
    "signature": {
      "return_type": "size_t",
      "parameters": "ZSTD_CStream* zcs, unsigned long long pledgedSrcSize",
      "calling_convention": null,
      "full_prototype": "size_t ZSTD_resetCStream(ZSTD_CStream* zcs, unsigned long long pledgedSrcSize)"
    },
    "documentation": {
      "summary": "",
      "description": "",
      "source_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "source_line": 2733
    },
    "evidence": {
      "discovered_by": "export_analyzer",
      "header_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "forwarded_to": null,
      "demangled_name": null
    },
    "mcp": {
      "version": "1.0",
      "input_schema": {
        "type": "object",
        "properties": {
          "zcs": {
            "type": "string",
            "description": "ZSTD_CStream*"
          },
          "pledgedSrcSize": {
            "type": "integer",
            "description": "unsigned long long"
          }
        },
        "required": [
          "zcs",
          "pledgedSrcSize"
        ]
      },
      "execution": {
        "method": "dll_import",
        "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
        "function_name": "ZSTD_resetCStream",
        "calling_convention": "stdcall",
        "charset": "ansi"
      }
    },
    "metadata": {
      "is_signed": false,
      "publisher": null
    },
    "execution": {
      "method": "dll_import",
      "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
      "function_name": "ZSTD_resetCStream",
      "calling_convention": "stdcall",
      "charset": "ansi"
    },
    "return_type": "size_t",
    "parameters": [
      {
        "name": "zcs",
        "type": "ZSTD_CStream*",
        "required": true,
        "description": "ZSTD_CStream*"
      },
      {
        "name": "pledgedSrcSize",
        "type": "unsigned long long",
        "required": true,
        "description": "unsigned long long"
      }
    ]
  },
  {
    "name": "ZSTD_resetDStream",
    "tool_id": "dll_ZSTD_resetDStream_176",
    "kind": "export",
    "ordinal": 176,
    "rva": "000690E0",
    "confidence": "guaranteed",
    "confidence_factors": {
      "has_signature": true,
      "has_documentation": false,
      "has_parameters": true,
      "has_return_type": true,
      "is_forwarded": true,
      "is_ordinal_only": false
    },
    "signature": {
      "return_type": "size_t",
      "parameters": "ZSTD_DStream* zds",
      "calling_convention": null,
      "full_prototype": "ZSTDLIB_STATIC_API size_t ZSTD_resetDStream(ZSTD_DStream* zds)"
    },
    "documentation": {
      "summary": "",
      "description": "",
      "source_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "source_line": 2801
    },
    "evidence": {
      "discovered_by": "export_analyzer",
      "header_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "forwarded_to": null,
      "demangled_name": null
    },
    "mcp": {
      "version": "1.0",
      "input_schema": {
        "type": "object",
        "properties": {
          "zds": {
            "type": "string",
            "description": "ZSTD_DStream*"
          }
        },
        "required": [
          "zds"
        ]
      },
      "execution": {
        "method": "dll_import",
        "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
        "function_name": "ZSTD_resetDStream",
        "calling_convention": "stdcall",
        "charset": "ansi"
      }
    },
    "metadata": {
      "is_signed": false,
      "publisher": null
    },
    "execution": {
      "method": "dll_import",
      "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
      "function_name": "ZSTD_resetDStream",
      "calling_convention": "stdcall",
      "charset": "ansi"
    },
    "return_type": "size_t",
    "parameters": [
      {
        "name": "zds",
        "type": "ZSTD_DStream*",
        "required": true,
        "description": "ZSTD_DStream*"
      }
    ]
  },
  {
    "name": "ZSTD_sequenceBound",
    "tool_id": "dll_ZSTD_sequenceBound_177",
    "kind": "export",
    "ordinal": 177,
    "rva": "00016970",
    "confidence": "guaranteed",
    "confidence_factors": {
      "has_signature": true,
      "has_documentation": true,
      "has_parameters": true,
      "has_return_type": true,
      "is_forwarded": true,
      "is_ordinal_only": false
    },
    "signature": {
      "return_type": "size_t",
      "parameters": "size_t srcSize",
      "calling_convention": null,
      "full_prototype": "ZSTDLIB_STATIC_API size_t ZSTD_sequenceBound(size_t srcSize)"
    },
    "documentation": {
      "summary": "! ZSTD_sequenceBound() :\n`srcSize` : size of the input buffer\n @return : upper-bound for the number of sequences that can be generated\n           from a buffer of srcSize bytes\n\n note : returns number of sequences - to get bytes, multiply by sizeof(ZSTD_Sequence).",
      "description": "! ZSTD_sequenceBound() :\n`srcSize` : size of the input buffer\n @return : upper-bound for the number of sequences that can be generated\n           from a buffer of srcSize bytes\n\n note : returns number of sequences - to get bytes, multiply by sizeof(ZSTD_Sequence).",
      "source_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "source_line": 1594
    },
    "evidence": {
      "discovered_by": "export_analyzer",
      "header_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "forwarded_to": null,
      "demangled_name": null
    },
    "mcp": {
      "version": "1.0",
      "input_schema": {
        "type": "object",
        "properties": {
          "srcSize": {
            "type": "integer",
            "description": "size_t"
          }
        },
        "required": [
          "srcSize"
        ]
      },
      "execution": {
        "method": "dll_import",
        "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
        "function_name": "ZSTD_sequenceBound",
        "calling_convention": "stdcall",
        "charset": "ansi"
      }
    },
    "metadata": {
      "is_signed": false,
      "publisher": null
    },
    "execution": {
      "method": "dll_import",
      "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
      "function_name": "ZSTD_sequenceBound",
      "calling_convention": "stdcall",
      "charset": "ansi"
    },
    "return_type": "size_t",
    "parameters": [
      {
        "name": "srcSize",
        "type": "size_t",
        "required": true,
        "description": "size_t"
      }
    ]
  },
  {
    "name": "ZSTD_sizeof_CCtx",
    "tool_id": "dll_ZSTD_sizeof_CCtx_178",
    "kind": "export",
    "ordinal": 178,
    "rva": "000169D0",
    "confidence": "guaranteed",
    "confidence_factors": {
      "has_signature": true,
      "has_documentation": true,
      "has_parameters": true,
      "has_return_type": true,
      "is_forwarded": true,
      "is_ordinal_only": false
    },
    "signature": {
      "return_type": "size_t",
      "parameters": "const ZSTD_CCtx* cctx",
      "calling_convention": null,
      "full_prototype": "ZSTDLIB_API size_t ZSTD_sizeof_CCtx(const ZSTD_CCtx* cctx)"
    },
    "documentation": {
      "summary": "! ZSTD_sizeof_*() : Requires v1.4.0+\n These functions give the _current_ memory usage of selected object.\n Note that object memory usage can evolve (increase or decrease) over time.",
      "description": "! ZSTD_sizeof_*() : Requires v1.4.0+\n These functions give the _current_ memory usage of selected object.\n Note that object memory usage can evolve (increase or decrease) over time.",
      "source_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "source_line": 1206
    },
    "evidence": {
      "discovered_by": "export_analyzer",
      "header_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "forwarded_to": null,
      "demangled_name": null
    },
    "mcp": {
      "version": "1.0",
      "input_schema": {
        "type": "object",
        "properties": {
          "cctx": {
            "type": "string",
            "description": "ZSTD_CCtx*"
          }
        },
        "required": [
          "cctx"
        ]
      },
      "execution": {
        "method": "dll_import",
        "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
        "function_name": "ZSTD_sizeof_CCtx",
        "calling_convention": "stdcall",
        "charset": "ansi"
      }
    },
    "metadata": {
      "is_signed": false,
      "publisher": null
    },
    "execution": {
      "method": "dll_import",
      "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
      "function_name": "ZSTD_sizeof_CCtx",
      "calling_convention": "stdcall",
      "charset": "ansi"
    },
    "return_type": "size_t",
    "parameters": [
      {
        "name": "cctx",
        "type": "const ZSTD_CCtx*",
        "required": true,
        "description": "const ZSTD_CCtx*"
      }
    ]
  },
  {
    "name": "ZSTD_sizeof_CDict",
    "tool_id": "dll_ZSTD_sizeof_CDict_179",
    "kind": "export",
    "ordinal": 179,
    "rva": "00016A90",
    "confidence": "guaranteed",
    "confidence_factors": {
      "has_signature": true,
      "has_documentation": false,
      "has_parameters": true,
      "has_return_type": true,
      "is_forwarded": true,
      "is_ordinal_only": false
    },
    "signature": {
      "return_type": "size_t",
      "parameters": "const ZSTD_CDict* cdict",
      "calling_convention": null,
      "full_prototype": "ZSTDLIB_API size_t ZSTD_sizeof_CDict(const ZSTD_CDict* cdict)"
    },
    "documentation": {
      "summary": "",
      "description": "",
      "source_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "source_line": 1210
    },
    "evidence": {
      "discovered_by": "export_analyzer",
      "header_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "forwarded_to": null,
      "demangled_name": null
    },
    "mcp": {
      "version": "1.0",
      "input_schema": {
        "type": "object",
        "properties": {
          "cdict": {
            "type": "string",
            "description": "ZSTD_CDict*"
          }
        },
        "required": [
          "cdict"
        ]
      },
      "execution": {
        "method": "dll_import",
        "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
        "function_name": "ZSTD_sizeof_CDict",
        "calling_convention": "stdcall",
        "charset": "ansi"
      }
    },
    "metadata": {
      "is_signed": false,
      "publisher": null
    },
    "execution": {
      "method": "dll_import",
      "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
      "function_name": "ZSTD_sizeof_CDict",
      "calling_convention": "stdcall",
      "charset": "ansi"
    },
    "return_type": "size_t",
    "parameters": [
      {
        "name": "cdict",
        "type": "const ZSTD_CDict*",
        "required": true,
        "description": "const ZSTD_CDict*"
      }
    ]
  },
  {
    "name": "ZSTD_sizeof_CStream",
    "tool_id": "dll_ZSTD_sizeof_CStream_180",
    "kind": "export",
    "ordinal": 180,
    "rva": "00016AC0",
    "confidence": "guaranteed",
    "confidence_factors": {
      "has_signature": true,
      "has_documentation": false,
      "has_parameters": true,
      "has_return_type": true,
      "is_forwarded": true,
      "is_ordinal_only": false
    },
    "signature": {
      "return_type": "size_t",
      "parameters": "const ZSTD_CStream* zcs",
      "calling_convention": null,
      "full_prototype": "ZSTDLIB_API size_t ZSTD_sizeof_CStream(const ZSTD_CStream* zcs)"
    },
    "documentation": {
      "summary": "",
      "description": "",
      "source_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "source_line": 1208
    },
    "evidence": {
      "discovered_by": "export_analyzer",
      "header_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "forwarded_to": null,
      "demangled_name": null
    },
    "mcp": {
      "version": "1.0",
      "input_schema": {
        "type": "object",
        "properties": {
          "zcs": {
            "type": "string",
            "description": "ZSTD_CStream*"
          }
        },
        "required": [
          "zcs"
        ]
      },
      "execution": {
        "method": "dll_import",
        "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
        "function_name": "ZSTD_sizeof_CStream",
        "calling_convention": "stdcall",
        "charset": "ansi"
      }
    },
    "metadata": {
      "is_signed": false,
      "publisher": null
    },
    "execution": {
      "method": "dll_import",
      "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
      "function_name": "ZSTD_sizeof_CStream",
      "calling_convention": "stdcall",
      "charset": "ansi"
    },
    "return_type": "size_t",
    "parameters": [
      {
        "name": "zcs",
        "type": "const ZSTD_CStream*",
        "required": true,
        "description": "const ZSTD_CStream*"
      }
    ]
  },
  {
    "name": "ZSTD_sizeof_DCtx",
    "tool_id": "dll_ZSTD_sizeof_DCtx_181",
    "kind": "export",
    "ordinal": 181,
    "rva": "00069110",
    "confidence": "guaranteed",
    "confidence_factors": {
      "has_signature": true,
      "has_documentation": false,
      "has_parameters": true,
      "has_return_type": true,
      "is_forwarded": true,
      "is_ordinal_only": false
    },
    "signature": {
      "return_type": "size_t",
      "parameters": "const ZSTD_DCtx* dctx",
      "calling_convention": null,
      "full_prototype": "ZSTDLIB_API size_t ZSTD_sizeof_DCtx(const ZSTD_DCtx* dctx)"
    },
    "documentation": {
      "summary": "",
      "description": "",
      "source_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "source_line": 1207
    },
    "evidence": {
      "discovered_by": "export_analyzer",
      "header_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "forwarded_to": null,
      "demangled_name": null
    },
    "mcp": {
      "version": "1.0",
      "input_schema": {
        "type": "object",
        "properties": {
          "dctx": {
            "type": "string",
            "description": "ZSTD_DCtx*"
          }
        },
        "required": [
          "dctx"
        ]
      },
      "execution": {
        "method": "dll_import",
        "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
        "function_name": "ZSTD_sizeof_DCtx",
        "calling_convention": "stdcall",
        "charset": "ansi"
      }
    },
    "metadata": {
      "is_signed": false,
      "publisher": null
    },
    "execution": {
      "method": "dll_import",
      "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
      "function_name": "ZSTD_sizeof_DCtx",
      "calling_convention": "stdcall",
      "charset": "ansi"
    },
    "return_type": "size_t",
    "parameters": [
      {
        "name": "dctx",
        "type": "const ZSTD_DCtx*",
        "required": true,
        "description": "const ZSTD_DCtx*"
      }
    ]
  },
  {
    "name": "ZSTD_sizeof_DDict",
    "tool_id": "dll_ZSTD_sizeof_DDict_182",
    "kind": "export",
    "ordinal": 182,
    "rva": "00064AB0",
    "confidence": "guaranteed",
    "confidence_factors": {
      "has_signature": true,
      "has_documentation": false,
      "has_parameters": true,
      "has_return_type": true,
      "is_forwarded": true,
      "is_ordinal_only": false
    },
    "signature": {
      "return_type": "size_t",
      "parameters": "const ZSTD_DDict* ddict",
      "calling_convention": null,
      "full_prototype": "ZSTDLIB_API size_t ZSTD_sizeof_DDict(const ZSTD_DDict* ddict)"
    },
    "documentation": {
      "summary": "",
      "description": "",
      "source_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "source_line": 1211
    },
    "evidence": {
      "discovered_by": "export_analyzer",
      "header_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "forwarded_to": null,
      "demangled_name": null
    },
    "mcp": {
      "version": "1.0",
      "input_schema": {
        "type": "object",
        "properties": {
          "ddict": {
            "type": "string",
            "description": "ZSTD_DDict*"
          }
        },
        "required": [
          "ddict"
        ]
      },
      "execution": {
        "method": "dll_import",
        "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
        "function_name": "ZSTD_sizeof_DDict",
        "calling_convention": "stdcall",
        "charset": "ansi"
      }
    },
    "metadata": {
      "is_signed": false,
      "publisher": null
    },
    "execution": {
      "method": "dll_import",
      "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
      "function_name": "ZSTD_sizeof_DDict",
      "calling_convention": "stdcall",
      "charset": "ansi"
    },
    "return_type": "size_t",
    "parameters": [
      {
        "name": "ddict",
        "type": "const ZSTD_DDict*",
        "required": true,
        "description": "const ZSTD_DDict*"
      }
    ]
  },
  {
    "name": "ZSTD_sizeof_DStream",
    "tool_id": "dll_ZSTD_sizeof_DStream_183",
    "kind": "export",
    "ordinal": 183,
    "rva": "00069110",
    "confidence": "guaranteed",
    "confidence_factors": {
      "has_signature": true,
      "has_documentation": false,
      "has_parameters": true,
      "has_return_type": true,
      "is_forwarded": true,
      "is_ordinal_only": false
    },
    "signature": {
      "return_type": "size_t",
      "parameters": "const ZSTD_DStream* zds",
      "calling_convention": null,
      "full_prototype": "ZSTDLIB_API size_t ZSTD_sizeof_DStream(const ZSTD_DStream* zds)"
    },
    "documentation": {
      "summary": "",
      "description": "",
      "source_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "source_line": 1209
    },
    "evidence": {
      "discovered_by": "export_analyzer",
      "header_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "forwarded_to": null,
      "demangled_name": null
    },
    "mcp": {
      "version": "1.0",
      "input_schema": {
        "type": "object",
        "properties": {
          "zds": {
            "type": "string",
            "description": "ZSTD_DStream*"
          }
        },
        "required": [
          "zds"
        ]
      },
      "execution": {
        "method": "dll_import",
        "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
        "function_name": "ZSTD_sizeof_DStream",
        "calling_convention": "stdcall",
        "charset": "ansi"
      }
    },
    "metadata": {
      "is_signed": false,
      "publisher": null
    },
    "execution": {
      "method": "dll_import",
      "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
      "function_name": "ZSTD_sizeof_DStream",
      "calling_convention": "stdcall",
      "charset": "ansi"
    },
    "return_type": "size_t",
    "parameters": [
      {
        "name": "zds",
        "type": "const ZSTD_DStream*",
        "required": true,
        "description": "const ZSTD_DStream*"
      }
    ]
  },
  {
    "name": "ZSTD_toFlushNow",
    "tool_id": "dll_ZSTD_toFlushNow_184",
    "kind": "export",
    "ordinal": 184,
    "rva": "00016AD0",
    "confidence": "guaranteed",
    "confidence_factors": {
      "has_signature": true,
      "has_documentation": true,
      "has_parameters": true,
      "has_return_type": true,
      "is_forwarded": true,
      "is_ordinal_only": false
    },
    "signature": {
      "return_type": "size_t",
      "parameters": "ZSTD_CCtx* cctx",
      "calling_convention": null,
      "full_prototype": "ZSTDLIB_STATIC_API size_t ZSTD_toFlushNow(ZSTD_CCtx* cctx)"
    },
    "documentation": {
      "summary": "! ZSTD_toFlushNow() :\n Tell how many bytes are ready to be flushed immediately.\n Useful for multithreading scenarios (nbWorkers >= 1).\n Probe the oldest active job, defined as oldest job not yet entirely flushed,\n and check its output buffer.\n@return : amount of data stored in oldest job and ready to be flushed immediately.\n if @return == 0, it means either :\n + there is no active job (could be checked with ZSTD_frameProgression()), or\n + oldest job is still actively compressing data,\n   but everything it has produced has also been flushed so far,\n   therefore flush speed is limited by production speed of oldest job\n   irrespective of the speed of concurrent (and newer) jobs.",
      "description": "! ZSTD_toFlushNow() :\n Tell how many bytes are ready to be flushed immediately.\n Useful for multithreading scenarios (nbWorkers >= 1).\n Probe the oldest active job, defined as oldest job not yet entirely flushed,\n and check its output buffer.\n@return : amount of data stored in oldest job and ready to be flushed immediately.\n if @return == 0, it means either :\n + there is no active job (could be checked with ZSTD_frameProgression()), or\n + oldest job is still actively compressing data,\n   but everything it has produced has also been flushed so far,\n   therefore flush speed is limited by production speed of oldest job\n   irrespective of the speed of concurrent (and newer) jobs.",
      "source_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "source_line": 2766
    },
    "evidence": {
      "discovered_by": "export_analyzer",
      "header_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "forwarded_to": null,
      "demangled_name": null
    },
    "mcp": {
      "version": "1.0",
      "input_schema": {
        "type": "object",
        "properties": {
          "cctx": {
            "type": "string",
            "description": "ZSTD_CCtx*"
          }
        },
        "required": [
          "cctx"
        ]
      },
      "execution": {
        "method": "dll_import",
        "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
        "function_name": "ZSTD_toFlushNow",
        "calling_convention": "stdcall",
        "charset": "ansi"
      }
    },
    "metadata": {
      "is_signed": false,
      "publisher": null
    },
    "execution": {
      "method": "dll_import",
      "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
      "function_name": "ZSTD_toFlushNow",
      "calling_convention": "stdcall",
      "charset": "ansi"
    },
    "return_type": "size_t",
    "parameters": [
      {
        "name": "cctx",
        "type": "ZSTD_CCtx*",
        "required": true,
        "description": "ZSTD_CCtx*"
      }
    ]
  },
  {
    "name": "ZSTD_versionNumber",
    "tool_id": "dll_ZSTD_versionNumber_185",
    "kind": "export",
    "ordinal": 185,
    "rva": "00003620",
    "confidence": "guaranteed",
    "confidence_factors": {
      "has_signature": true,
      "has_documentation": true,
      "has_parameters": true,
      "has_return_type": true,
      "is_forwarded": true,
      "is_ordinal_only": false
    },
    "signature": {
      "return_type": "unsigned",
      "parameters": "void",
      "calling_convention": null,
      "full_prototype": "ZSTDLIB_API unsigned ZSTD_versionNumber(void)"
    },
    "documentation": {
      "summary": "! ZSTD_versionNumber() :\n Return runtime library version, the value is (MAJOR*100*100 + MINOR*100 + RELEASE).",
      "description": "! ZSTD_versionNumber() :\n Return runtime library version, the value is (MAJOR*100*100 + MINOR*100 + RELEASE).",
      "source_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "source_line": 119
    },
    "evidence": {
      "discovered_by": "export_analyzer",
      "header_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "forwarded_to": null,
      "demangled_name": null
    },
    "mcp": {
      "version": "1.0",
      "input_schema": {
        "type": "object",
        "properties": {},
        "required": []
      },
      "execution": {
        "method": "dll_import",
        "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
        "function_name": "ZSTD_versionNumber",
        "calling_convention": "stdcall",
        "charset": "ansi"
      }
    },
    "metadata": {
      "is_signed": false,
      "publisher": null
    },
    "execution": {
      "method": "dll_import",
      "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
      "function_name": "ZSTD_versionNumber",
      "calling_convention": "stdcall",
      "charset": "ansi"
    },
    "return_type": "unsigned"
  },
  {
    "name": "ZSTD_versionString",
    "tool_id": "dll_ZSTD_versionString_186",
    "kind": "export",
    "ordinal": 186,
    "rva": "00003630",
    "confidence": "guaranteed",
    "confidence_factors": {
      "has_signature": true,
      "has_documentation": true,
      "has_parameters": true,
      "has_return_type": true,
      "is_forwarded": true,
      "is_ordinal_only": false
    },
    "signature": {
      "return_type": "const char*",
      "parameters": "void",
      "calling_convention": null,
      "full_prototype": "ZSTDLIB_API const char* ZSTD_versionString(void)"
    },
    "documentation": {
      "summary": "! ZSTD_versionString() :\n Return runtime library version, like \"1.4.5\". Requires v1.3.0+.",
      "description": "! ZSTD_versionString() :\n Return runtime library version, like \"1.4.5\". Requires v1.3.0+.",
      "source_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "source_line": 128
    },
    "evidence": {
      "discovered_by": "export_analyzer",
      "header_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "forwarded_to": null,
      "demangled_name": null
    },
    "mcp": {
      "version": "1.0",
      "input_schema": {
        "type": "object",
        "properties": {},
        "required": []
      },
      "execution": {
        "method": "dll_import",
        "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
        "function_name": "ZSTD_versionString",
        "calling_convention": "stdcall",
        "charset": "ansi"
      }
    },
    "metadata": {
      "is_signed": false,
      "publisher": null
    },
    "execution": {
      "method": "dll_import",
      "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
      "function_name": "ZSTD_versionString",
      "calling_convention": "stdcall",
      "charset": "ansi"
    },
    "return_type": "const char*"
  },
  {
    "name": "ZSTD_writeSkippableFrame",
    "tool_id": "dll_ZSTD_writeSkippableFrame_187",
    "kind": "export",
    "ordinal": 187,
    "rva": "000176C0",
    "confidence": "guaranteed",
    "confidence_factors": {
      "has_signature": true,
      "has_documentation": true,
      "has_parameters": true,
      "has_return_type": true,
      "is_forwarded": true,
      "is_ordinal_only": false
    },
    "signature": {
      "return_type": "size_t",
      "parameters": "void* dst, size_t dstCapacity, const void* src, size_t srcSize, unsigned magicVariant",
      "calling_convention": null,
      "full_prototype": "ZSTDLIB_STATIC_API size_t ZSTD_writeSkippableFrame(void* dst, size_t dstCapacity, const void* src, size_t srcSize, unsigned magicVariant)"
    },
    "documentation": {
      "summary": "! ZSTD_writeSkippableFrame() :\nGenerates a zstd skippable frame containing data given by src, and writes it to dst buffer.\n\nSkippable frames begin with a 4-byte magic number. There are 16 possible choices of magic number,\nranging from ZSTD_MAGIC_SKIPPABLE_START to ZSTD_MAGIC_SKIPPABLE_START+15.\nAs such, the parameter magicVariant controls the exact skippable frame magic number variant used,\nso the magic number used will be ZSTD_MAGIC_SKIPPABLE_START + magicVariant.\n\nReturns an error if destination buffer is not large enough, if the source size is not representable\nwith a 4-byte unsigned int, or if the parameter magicVariant is greater than 15 (and therefore invalid).\n\n@return : number of bytes written or a ZSTD error.",
      "description": "! ZSTD_writeSkippableFrame() :\nGenerates a zstd skippable frame containing data given by src, and writes it to dst buffer.\n\nSkippable frames begin with a 4-byte magic number. There are 16 possible choices of magic number,\nranging from ZSTD_MAGIC_SKIPPABLE_START to ZSTD_MAGIC_SKIPPABLE_START+15.\nAs such, the parameter magicVariant controls the exact skippable frame magic number variant used,\nso the magic number used will be ZSTD_MAGIC_SKIPPABLE_START + magicVariant.\n\nReturns an error if destination buffer is not large enough, if the source size is not representable\nwith a 4-byte unsigned int, or if the parameter magicVariant is greater than 15 (and therefore invalid).\n\n@return : number of bytes written or a ZSTD error.",
      "source_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "source_line": 1725
    },
    "evidence": {
      "discovered_by": "export_analyzer",
      "header_file": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\include\\zstd.h",
      "forwarded_to": null,
      "demangled_name": null
    },
    "mcp": {
      "version": "1.0",
      "input_schema": {
        "type": "object",
        "properties": {
          "dst": {
            "type": "string",
            "description": "void*"
          },
          "dstCapacity": {
            "type": "integer",
            "description": "size_t"
          },
          "src": {
            "type": "string",
            "description": "void*"
          },
          "srcSize": {
            "type": "integer",
            "description": "size_t"
          },
          "magicVariant": {
            "type": "integer",
            "description": "unsigned"
          }
        },
        "required": [
          "dst",
          "dstCapacity",
          "src",
          "srcSize",
          "magicVariant"
        ]
      },
      "execution": {
        "method": "dll_import",
        "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
        "function_name": "ZSTD_writeSkippableFrame",
        "calling_convention": "stdcall",
        "charset": "ansi"
      }
    },
    "metadata": {
      "is_signed": false,
      "publisher": null
    },
    "execution": {
      "method": "dll_import",
      "dll_path": "C:\\Users\\evanw\\Downloads\\capstone_project\\mcp-factory\\tests\\fixtures\\vcpkg_installed\\x64-windows\\bin\\zstd.dll",
      "function_name": "ZSTD_writeSkippableFrame",
      "calling_convention": "stdcall",
      "charset": "ansi"
    },
    "return_type": "size_t",
    "parameters": [
      {
        "name": "dst",
        "type": "void*",
        "required": true,
        "description": "void*"
      },
      {
        "name": "dstCapacity",
        "type": "size_t",
        "required": true,
        "description": "size_t"
      },
      {
        "name": "src",
        "type": "const void*",
        "required": true,
        "description": "const void*"
      },
      {
        "name": "srcSize",
        "type": "size_t",
        "required": true,
        "description": "size_t"
      },
      {
        "name": "magicVariant",
        "type": "unsigned",
        "required": true,
        "description": "unsigned"
      }
    ]
  }
]""")

INVOCABLE_MAP = {inv["name"]: inv for inv in INVOCABLES}


_C_TO_JSON_TYPE = {
    "int": "integer", "unsigned": "integer", "unsigned int": "integer",
    "long": "integer", "unsigned long": "integer", "size_t": "integer",
    "uint32_t": "integer", "uint64_t": "integer", "int32_t": "integer",
    "int64_t": "integer", "short": "integer", "unsigned short": "integer",
    "float": "number", "double": "number",
    "bool": "boolean",
}


def _c_type_to_json_type(c_type: str) -> str:
    """Map a C type string to the closest JSON schema primitive type."""
    t = c_type.lower().strip().rstrip("*").strip()
    if t in _C_TO_JSON_TYPE:
        return _C_TO_JSON_TYPE[t]
    # pointer / char* / string variants → string
    if "char" in t or "string" in t or "str" == t:
        return "string"
    # anything containing 'int' or 'long' → integer
    if "int" in t or "long" in t:
        return "integer"
    return "string"


def _build_openai_functions():
    """Convert the invocable list into OpenAI function-calling schema objects."""
    fns = []
    for inv in INVOCABLES:
        props = {}
        required = []
        for p in inv.get("parameters", []):
            json_type = _c_type_to_json_type(p.get("type", "string"))
            props[p["name"]] = {
                "type": json_type,
                "description": p.get("description", p.get("type", "")),
            }
            if p.get("required", False):
                required.append(p["name"])
        fns.append({
            "type": "function",
            "function": {
                "name": inv["name"],
                "description": inv.get("description") or f"Invoke {inv['name']} from zstd",
                "parameters": {
                    "type": "object",
                    "properties": props,
                    "required": required,
                },
            },
        })
    return fns


OPENAI_FUNCTIONS = _build_openai_functions()


# ── Semantic tool retrieval ─────────────────────────────────────────────────
# Lazily embeds all tool descriptions on first chat request, then returns the
# top-K tools most semantically relevant to the user's message.  This keeps
# every binary's full export list discoverable while respecting OpenAI's
# 128-tool hard cap.  Falls back to OPENAI_FUNCTIONS[:128] on any error.
_TOOL_EMBED_CACHE: list = []
_TOOL_EMBED_LOCK  = threading.Lock()
_TOOL_EMBED_K     = 60   # tools sent to OpenAI per request


def _retrieve_tools(user_message: str, client, k: int = _TOOL_EMBED_K) -> list:
    """Return the top-k tools most relevant to user_message (semantic search)."""
    global _TOOL_EMBED_CACHE
    if not OPENAI_FUNCTIONS:
        return []
    if len(OPENAI_FUNCTIONS) <= k:
        return OPENAI_FUNCTIONS  # small enough — no retrieval needed
    try:
        import math

        def _norm(v: list) -> list:
            mag = math.sqrt(sum(x * x for x in v))
            return [x / mag for x in v] if mag else v

        def _dot(a: list, b: list) -> float:
            return sum(x * y for x, y in zip(a, b))

        with _TOOL_EMBED_LOCK:
            if not _TOOL_EMBED_CACHE:
                texts = [
                    f"{fn['function']['name']}: {fn['function']['description']}"
                    for fn in OPENAI_FUNCTIONS
                ]
                resp = client.embeddings.create(
                    model="text-embedding-3-small", input=texts
                )
                _TOOL_EMBED_CACHE[:] = [
                    _norm(item.embedding)
                    for item in sorted(resp.data, key=lambda x: x.index)
                ]

        q_resp = client.embeddings.create(
            model="text-embedding-3-small", input=[user_message]
        )
        q = _norm(q_resp.data[0].embedding)
        scores = [(_dot(q, vec), i) for i, vec in enumerate(_TOOL_EMBED_CACHE)]
        scores.sort(reverse=True)
        return [OPENAI_FUNCTIONS[i] for _, i in scores[:k]]
    except Exception:
        return OPENAI_FUNCTIONS[:128]  # graceful fallback


# ── Execution helpers ───────────────────────────────────────────────────────

def _execute_tool(name: str, args: dict) -> str:
    inv = INVOCABLE_MAP.get(name)
    if not inv:
        return f"Unknown tool: {name}"
    # Support both flat {"execution": {...}} and rich MCP {"mcp": {"execution": {...}}}
    execution = inv.get("execution") or inv.get("mcp", {}).get("execution", {})
    method = execution.get("method", "")
    if method == "dll_import":
        return _execute_dll(inv, execution, args)
    if method == "gui_action":
        return _execute_gui(execution, name, args)
    return _execute_cli(execution, name, args)


# C type map used by _execute_dll
_CTYPES_RESTYPE = {
    "void":           None,
    "bool":           ctypes.c_bool,
    "int":            ctypes.c_int,
    "unsigned":       ctypes.c_uint,
    "unsigned int":   ctypes.c_uint,
    "long":           ctypes.c_long,
    "unsigned long":  ctypes.c_ulong,
    "size_t":         ctypes.c_size_t,
    "float":          ctypes.c_float,
    "double":         ctypes.c_double,
    "char*":          ctypes.c_char_p,
    "const char*":    ctypes.c_char_p,
    "char *":         ctypes.c_char_p,
    "const char *":   ctypes.c_char_p,
}
_CTYPES_ARGTYPE = {
    "int":            ctypes.c_int,
    "unsigned":       ctypes.c_uint,
    "unsigned int":   ctypes.c_uint,
    "long":           ctypes.c_long,
    "unsigned long":  ctypes.c_ulong,
    "size_t":         ctypes.c_size_t,
    "float":          ctypes.c_float,
    "double":         ctypes.c_double,
    "bool":           ctypes.c_bool,
    "string":         ctypes.c_char_p,
    "str":            ctypes.c_char_p,
    "char*":          ctypes.c_char_p,
    "const char*":    ctypes.c_char_p,
    "char *":         ctypes.c_char_p,
    "const char *":   ctypes.c_char_p,
}


def _resolve_dll_path(raw: str) -> str:
    """Return an absolute path for *raw*, searching likely anchors."""
    from pathlib import Path as _P
    p = _P(raw)
    if p.is_absolute() and p.exists():
        return str(p)
    # server lives at generated/<component>/server.py → project root is 2 dirs up
    project_root = _P(__file__).resolve().parent.parent.parent
    candidate = project_root / raw
    if candidate.exists():
        return str(candidate)
    # also try relative to server's own directory
    local = _P(__file__).resolve().parent / raw
    if local.exists():
        return str(local)
    return raw  # best-effort; let ctypes give the real error


def _execute_dll(inv: dict, execution: dict, args: dict) -> str:
    dll_path  = _resolve_dll_path(execution.get("dll_path", ""))
    func_name = execution.get("function_name", "")

    # Return type: flat "return_type" field takes priority; fall back to
    # signature.return_type from the rich MCP schema.
    ret_str = (
        inv.get("return_type")
        or (inv.get("signature") or {}).get("return_type", "unknown")
        or "unknown"
    ).strip()
    restype = _CTYPES_RESTYPE.get(ret_str.lower(), ctypes.c_size_t)

    # Parameter list: flat "parameters" list or parsed from signature string
    params = list(inv.get("parameters") or [])
    if not params:
        sig_str = (inv.get("signature") or {}).get("parameters", "")
        if sig_str:
            for chunk in sig_str.split(","):
                tokens = chunk.strip().split()
                if len(tokens) >= 2:
                    # e.g. ["const", "char*", "buf"] or ["size_t", "n"]
                    raw_type = " ".join(tokens[:-1]).lower().strip("*").rstrip()
                    pname    = tokens[-1].lstrip("*")
                    params.append({"name": pname, "type": raw_type})

    try:
        lib = ctypes.CDLL(dll_path)
        fn  = getattr(lib, func_name)
        fn.restype = restype

        # Build ctypes arg list from the named args dict
        c_args = []
        if params and args:
            for p in params:
                pname = p.get("name", "")
                ptype = p.get("type", "string").lower().strip("*").rstrip()
                val   = args.get(pname)
                if val is None:
                    continue
                atype = _CTYPES_ARGTYPE.get(ptype, ctypes.c_char_p)
                if atype == ctypes.c_char_p:
                    c_args.append(ctypes.c_char_p(str(val).encode()))
                else:
                    c_args.append(atype(int(val)))
        elif args:
            # No type metadata — guess from Python value type
            for v in args.values():
                if isinstance(v, bool):
                    c_args.append(ctypes.c_bool(v))
                elif isinstance(v, int):
                    c_args.append(ctypes.c_size_t(v))
                elif isinstance(v, float):
                    c_args.append(ctypes.c_double(v))
                elif isinstance(v, str):
                    c_args.append(ctypes.c_char_p(v.encode()))

        result = fn(*c_args)

        # Decode bytes result from char* functions
        if restype == ctypes.c_char_p:
            if isinstance(result, bytes):
                return f"Returned: {result.decode(errors='replace')}"
        return f"Returned: {result}"
    except Exception as exc:
        return f"DLL call error: {exc}"


def _execute_cli(execution: dict, name: str, args: dict) -> str:
    target = (
        execution.get("executable_path")
        or execution.get("target_path")
        or execution.get("dll_path", "")
    )
    if not target:
        return f"CLI error: no executable path configured for '{name}'"
    from pathlib import Path as _Path
    exe_stem = _Path(target).stem.lower()
    if exe_stem == name.lower():
        try:
            subprocess.Popen([target])
            return f"Launched {_Path(target).name}"
        except Exception as exc:
            return f"CLI error: {exc}"
    cmd = [target, name] + [str(v) for v in args.values()]
    creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        r = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=10,
            creationflags=creation_flags,
        )
        return r.stdout or r.stderr or f"exit_code={r.returncode}"
    except Exception as exc:
        return f"CLI error: {exc}"


# ── GUI app state (persistent across HTTP requests) ─────────────────────────
# A single-user demo stores one live pywinauto Application per exe_path.
# The app stays alive between calls so "type hello" → "save as test.txt"
# works as two separate HTTP requests against the same window.

_GUI_APP_LOCK = threading.Lock()
_GUI_APP_INSTANCES: dict = {}  # exe_path -> (Application, main_window)


def _ensure_gui_app(exe_path: str):
    """Return (app, win) for exe_path, launching the app if not already running.

    Tries the win32 backend first (classic Win32 apps).  If no window is found,
    automatically retries with the uia backend (WinUI3 / Win11 apps like
    Notepad 10.x).
    """
    import time
    import subprocess
    import re
    from pathlib import Path
    try:
        from pywinauto.application import Application  # type: ignore
    except ImportError:
        raise RuntimeError(
            "pywinauto is not installed. "
            "Run: pip install pywinauto"
        )

    with _GUI_APP_LOCK:
        entry = _GUI_APP_INSTANCES.get(exe_path)
        if entry is not None:
            app, win, backend = entry
            try:
                win.exists()
                return app, win
            except Exception:
                _GUI_APP_INSTANCES.pop(exe_path, None)

        last_exc = None

        # ── Attempt A: win32 backend (classic Win32 apps) ─────────────────
        # Run inside a thread so a hung top_window() gets a hard deadline.
        # STARTUPINFO SW_SHOWMINNOACTIVE keeps the window minimised in taskbar.
        import concurrent.futures as _cf

        def _launch_win32():
            _app = Application(backend="win32")
            si = subprocess.STARTUPINFO()
            si.dwFlags = 0x00000001    # STARTF_USESHOWWINDOW
            si.wShowWindow = 7         # SW_SHOWMINNOACTIVE
            _proc = subprocess.Popen([exe_path], startupinfo=si)
            time.sleep(1.8)
            _app = Application(backend="win32").connect(process=_proc.pid, timeout=3)
            _win = _app.top_window()
            _win.handle  # raises immediately if no window under this PID
            return _app, _win

        with _cf.ThreadPoolExecutor(max_workers=1) as _pool:
            _fut = _pool.submit(_launch_win32)
            try:
                app, win = _fut.result(timeout=7)
                _GUI_APP_INSTANCES[exe_path] = (app, win, "win32")
                return app, win
            except Exception as exc:
                last_exc = exc  # win32 failed — fall through to UIA

        # ── Attempt B: UIA + shell-launch (MSIX / WinUI3 apps) ───────────
        # Win11 Notepad (and modern Calculator) use MSIX stubs that exit
        # immediately — pywinauto loses the PID.  Strategy:
        #
        #   1. Snapshot existing top-level HWNDs with win32gui (preferred).
        #   2. Shell-launch so Windows resolves the MSIX package.
        #   3. Wait for the real process to create its window.
        #   4. Diff HWNDs → the new window IS the app; connect by handle.
        #
        # This bypasses all title/path guessing and works regardless of
        # what title or process-name Win11 Notepad registers.
        # Falls back to title-pattern loop if win32gui is unavailable.
        try:
            # ── B1: HWND-diff via win32gui (reliable) ─────────────────────
            try:
                import win32gui as _w32gui  # type: ignore

                def _snap():
                    _hwnds: set = set()
                    def _cb(h, _):
                        if _w32gui.IsWindowVisible(h) and _w32gui.GetWindowText(h):
                            _hwnds.add(h)
                    _w32gui.EnumWindows(_cb, None)
                    return _hwnds

                before = _snap()
                subprocess.Popen(f'start "" "{exe_path}"', shell=True)
                time.sleep(4.0)
                after = _snap()
                new_hwnds = after - before
                if not new_hwnds:
                    raise RuntimeError(
                        "No new top-level windows appeared after launching "
                        f"{exe_path!r} — HWND diff came up empty"
                    )
                hwnd = next(iter(new_hwnds))
                app = Application(backend="uia").connect(handle=hwnd, timeout=5)
            except ImportError:
                # win32gui not available — fall back to title patterns
                subprocess.Popen(f'start "" "{exe_path}"', shell=True)
                time.sleep(3.5)
                exe_stem = Path(exe_path).stem
                app = None
                for connect_kw in [
                    {"path": exe_path},
                    {"title_re": f"(?i).*{re.escape(exe_stem)}.*"},
                    {"title_re": "(?i).*Untitled.*"},
                    {"title_re": "(?i).*New Tab.*"},
                ]:
                    try:
                        app = Application(backend="uia").connect(timeout=5, **connect_kw)
                        break
                    except Exception as exc:
                        last_exc = exc
                if app is None:
                    raise RuntimeError(
                        f"UIA connect failed after shell-launch: {last_exc}"
                    )

            win = app.top_window()
            _GUI_APP_INSTANCES[exe_path] = (app, win, "uia")
            return app, win
        except Exception as exc:
            last_exc = exc

        raise RuntimeError(
            f"Could not start GUI app {exe_path!r}: {last_exc}"
        )


def _execute_gui(execution: dict, name: str, args: dict) -> str:
    """Execute a GUI action against a live pywinauto-controlled window.

    Supported action_type values:
        menu_click  - navigate a menu path, e.g. ["File", "Save As"]
        type_text   - type text into the active edit control
        get_text    - return text content of the main edit control
        save_as     - open File→Save As dialog and save with given filename
        close_app   - kill the application process
    """
    import time
    exe_path = execution.get("exe_path", "")
    action_type = execution.get("action_type", "menu_click")
    menu_path = execution.get("menu_path", [])

    try:
        app, win = _ensure_gui_app(exe_path)
    except Exception as exc:
        return f"GUI launch error: {exc}"

    try:
        # ── type_text ────────────────────────────────────────────────────────
        if action_type == "type_text":
            text = args.get("text", "")
            win.set_focus()
            typed = False
            # Try progressively broader strategies to reach the edit control
            for class_name in ("RichEditD2DPT", "Edit", "RichEdit20W", "RICHEDIT50W"):
                try:
                    edit = win.child_window(class_name=class_name)
                    edit.set_focus()
                    edit.type_keys(text, with_spaces=True)
                    typed = True
                    break
                except Exception:
                    pass
            if not typed:
                # UIA fallback — find Document or text area by control type
                try:
                    doc = win.child_window(control_type="Document")
                    doc.set_focus()
                    doc.type_keys(text, with_spaces=True)
                    typed = True
                except Exception:
                    pass
            if not typed:
                # Last resort: type into the top-level window
                win.type_keys(text, with_spaces=True)
            return f"Typed: {repr(text)}"

        # ── get_text ─────────────────────────────────────────────────────────
        elif action_type == "get_text":
            for class_name in ("RichEditD2DPT", "Edit", "RichEdit20W", "RICHEDIT50W"):
                try:
                    edit = win.child_window(class_name=class_name)
                    return edit.window_text()
                except Exception:
                    pass
            try:
                doc = win.child_window(control_type="Document")
                return doc.window_text()
            except Exception:
                pass
            return win.window_text()

        # ── save_as ──────────────────────────────────────────────────────────
        elif action_type == "save_as":
            filename = (
                args.get("filename")
                or args.get("name")
                or args.get("file")
                or "output.txt"
            )
            win.set_focus()
            time.sleep(0.2)

            # Trigger Save As — try multiple methods in order
            triggered = False
            for _trigger in (
                lambda: win.menu_select("File->Save As"),       # classic Win32
                lambda: win.type_keys("^+s"),                   # Ctrl+Shift+S (Win11 Notepad)
                lambda: win.type_keys("%fa"),                   # Alt+F A
                lambda: win.type_keys("{VK_MENU}fa"),           # same via VK
            ):
                try:
                    _trigger()
                    triggered = True
                    break
                except Exception:
                    pass
            if not triggered:
                return "Could not trigger Save As dialog"

            time.sleep(1.2)  # Wait for the shell dialog to appear

            # Locate the Save As dialog — try multiple title patterns
            dlg = None
            for _title in ("Save As", "Save as", "Save As.*", ".*Save.*"):
                try:
                    dlg = app.window(title_re=_title)
                    dlg.wait("visible", timeout=4)
                    break
                except Exception:
                    dlg = None

            if dlg is None:
                try:
                    dlg = app.top_window()
                except Exception as exc:
                    return f"Save As dialog not found: {exc}"

            try:
                fn_ctrl = None
                for _lookup in (
                    lambda: dlg.child_window(title="File name:", control_type="Edit"),
                    lambda: dlg.child_window(class_name="Edit", found_index=0),
                    lambda: dlg.child_window(control_type="ComboBox").child_window(class_name="Edit"),
                    lambda: dlg.child_window(auto_id="1001"),
                ):
                    try:
                        fn_ctrl = _lookup()
                        fn_ctrl.wrapper_object()
                        break
                    except Exception:
                        fn_ctrl = None

                if fn_ctrl is not None:
                    fn_ctrl.set_focus()
                    fn_ctrl.set_text("")
                    fn_ctrl.type_keys(filename, with_spaces=True)
                else:
                    import pywinauto.keyboard as _kb  # type: ignore
                    _kb.send_keys(filename)

                time.sleep(0.3)
                try:
                    dlg.child_window(title="Save", control_type="Button").click()
                except Exception:
                    try:
                        dlg.child_window(title_re="Save.*", control_type="Button").click()
                    except Exception:
                        import pywinauto.keyboard as _kb  # type: ignore
                        _kb.send_keys("{ENTER}")

                time.sleep(0.6)
                return f"Saved as: {filename}"
            except Exception as dlg_exc:
                return f"Save As dialog interaction error: {dlg_exc}"

        # ── close_app ────────────────────────────────────────────────────────
        elif action_type == "close_app":
            with _GUI_APP_LOCK:
                _GUI_APP_INSTANCES.pop(exe_path, None)
            try:
                app.kill()
            except Exception:
                pass
            return "Application closed"

        # ── menu_click ───────────────────────────────────────────────────────
        elif action_type == "menu_click":
            if not menu_path:
                return "No menu_path specified for menu_click action"
            try:
                menu_str = "->".join(menu_path)  # pywinauto separator
                win.menu_select(menu_str)
                time.sleep(0.3)
                sep = " -> "
                return f"Clicked menu: {sep.join(menu_path)}"
            except Exception as menu_exc:
                return f"Menu click error for {menu_path}: {menu_exc}"

        else:
            return f"Unknown GUI action_type: {action_type!r}"

    except Exception as exc:
        # If something went wrong (window died, etc.) clear the cached instance
        with _GUI_APP_LOCK:
            _GUI_APP_INSTANCES.pop(exe_path, None)
        return f"GUI action error ({action_type}): {exc}"


# ── Routes ──────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return send_from_directory("static", "index.html")


@app.route("/tools", methods=["GET"])
def list_tools():
    return jsonify([inv["name"] for inv in INVOCABLES])


@app.route("/invoke", methods=["POST"])
def invoke():
    body = request.json or {}
    name = body.get("tool", "")
    args = body.get("args", {})
    result = _execute_tool(name, args)
    return jsonify({"result": result})


@app.route("/chat", methods=["POST"])
def chat():
    body = request.json or {}
    history = body.get("history", [])          # [{role, content}, ...]
    user_message = body.get("message", "").strip()
    if not user_message:
        return jsonify({"error": "message is required"}), 400

    client = OpenAI(
        api_key=os.getenv("OPENAI_API_KEY"),
        base_url=os.getenv("OPENAI_BASE_URL") or "https://api.openai.com/v1",
    )
    model = os.getenv("OPENAI_DEPLOYMENT", "gpt-4o-mini")

    system_prompt = (
        "You are a helpful assistant with access to binary tools from zstd. "
        "When a user asks you to perform a multi-step task, issue as many tool calls as possible in a single response — only wait for results when a later step strictly depends on the output of an earlier one. "
        "Explain what each tool does and report results clearly."
    )

    messages = (
        [{"role": "system", "content": system_prompt}]
        + history
        + [{"role": "user", "content": user_message}]
    )

    _tools = _retrieve_tools(user_message, client) or None
    response = client.chat.completions.create(
        model=model,
        messages=messages,
        tools=_tools or None,
        tool_choice="auto" if _tools else "none",
    )

    msg = response.choices[0].message
    tool_outputs = []

    if msg.tool_calls:
        tool_messages = []
        for tc in msg.tool_calls:
            fn_name = tc.function.name
            try:
                fn_args = json.loads(tc.function.arguments)
            except json.JSONDecodeError:
                fn_args = {}
            result = _execute_tool(fn_name, fn_args)
            tool_outputs.append({"name": fn_name, "args": fn_args, "result": result})
            tool_messages.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": result,
            })

        # Feed tool results back for a natural-language summary.
        messages.append(msg.model_dump(exclude_none=True))
        messages.extend(tool_messages)
        followup = client.chat.completions.create(model=model, messages=messages)
        reply = followup.choices[0].message.content or ""
    else:
        reply = msg.content or ""

    # Return updated history (strip system message so the client can replay it).
    updated_history = messages[1:]

    return jsonify({
        "reply": reply,
        "tool_outputs": tool_outputs,
        "updated_history": updated_history,
    })


@app.route("/download/invocables")
def download_invocables():
    """Serve the raw invocables list as a JSON download."""
    resp = app.response_class(
        response=json.dumps(INVOCABLES, indent=2),
        status=200,
        mimetype="application/json",
    )
    resp.headers["Content-Disposition"] = "attachment; filename=selected-invocables.json"
    return resp


if __name__ == "__main__":
    app.run(port=5000, debug=False)
