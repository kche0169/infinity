# Copyright(C) 2024 InfiniFlow, Inc. All rights reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import re
import functools
import inspect
from typing import Any
import pandas as pd
import polars as pl
from sqlglot import condition
import sqlglot.expressions as exp
import numpy as np
from infinity_embedded.errors import ErrorCode
from infinity_embedded.common import InfinityException, SparseVector, Array
from infinity_embedded.local_infinity.types import build_result, logic_type_to_dtype
from infinity_embedded.utils import binary_exp_to_paser_exp
from infinity_embedded.embedded_infinity_ext import WrapInExpr, WrapParsedExpr, WrapFunctionExpr, \
    WrapColumnExpr, WrapConstantExpr, ParsedExprType, LiteralType
from infinity_embedded.embedded_infinity_ext import WrapEmbeddingType, WrapColumnDef, WrapDataType, LogicalType, \
    EmbeddingDataType, WrapSparseType, ConstraintType


def traverse_conditions(cons, fn=None):
    if isinstance(cons, exp.Alias):
        expr = traverse_conditions(cons.args['this'])
        expr.alias_name = cons.alias
        return expr

    if isinstance(cons, exp.Binary):
        parsed_expr = WrapParsedExpr()
        function_expr = WrapFunctionExpr()
        function_expr.func_name = binary_exp_to_paser_exp(
            cons.key.lower())  # key is the function name cover to >, <, >=, <=, =, and, or, etc.

        arguments = []
        for value in cons.hashable_args:
            if fn:
                expr = fn(value)
            else:
                expr = traverse_conditions(value)
            arguments.append(expr)
        function_expr.arguments = arguments

        parsed_expr.type = ParsedExprType.kFunction
        parsed_expr.function_expr = function_expr

        return parsed_expr
    elif isinstance(cons, exp.Not) and isinstance(cons.args['this'], exp.In) == False:
        parsed_expr = WrapParsedExpr()
        function_expr = WrapFunctionExpr()
        function_expr.func_name = "not"
        arguments = []
        for value in cons.hashable_args:
            if fn:
                expr = fn(value)
            else:
                expr = traverse_conditions(value)
            arguments.append(expr)
        function_expr.arguments = arguments
        parsed_expr.type = ParsedExprType.kFunction
        parsed_expr.function_expr = function_expr
        return parsed_expr
    elif isinstance(cons, exp.Column):
        parsed_expr = WrapParsedExpr()
        column_expr = WrapColumnExpr()
        column_name = [cons.alias_or_name.lower()]
        if cons.alias_or_name == "*":
            column_expr.star = True
        else:
            column_expr.star = False
        column_expr.names = column_name

        parsed_expr.type = ParsedExprType.kColumn
        parsed_expr.column_expr = column_expr

        return parsed_expr

    elif isinstance(cons, exp.Boolean):
        parsed_expr = WrapParsedExpr()
        constant_expr = WrapConstantExpr()
        constant_expr.literal_type = LiteralType.kBoolean
        constant_expr.bool_value = cons.this
        parsed_expr.type = ParsedExprType.kConstant
        parsed_expr.constant_expr = constant_expr
        return parsed_expr

    elif isinstance(cons, exp.Literal):
        parsed_expr = WrapParsedExpr()
        constant_expr = WrapConstantExpr()

        if cons.is_int:
            constant_expr.literal_type = LiteralType.kInteger
            constant_expr.i64_value = int(cons.output_name)
        elif cons.is_number:
            constant_expr.literal_type = LiteralType.kDouble
            constant_expr.f64_value = float(cons.output_name)
        elif cons.is_string:
            constant_expr.literal_type = LiteralType.kString
            constant_expr.str_value = cons.output_name
        else:
            raise Exception(f"unknown literal type: {cons}")

        parsed_expr.type = ParsedExprType.kConstant
        parsed_expr.constant_expr = constant_expr
        return parsed_expr

    elif isinstance(cons, exp.Paren):
        for value in cons.hashable_args:
            return traverse_conditions(value)

    elif isinstance(cons, exp.Neg):
        func_expr = WrapFunctionExpr()
        func_expr.func_name = '-'
        func_expr.arguments = [parse_expr(cons.hashable_args[0])]
        parsed_expr = WrapParsedExpr()
        parsed_expr.type = ParsedExprType.kFunction
        parsed_expr.function_expr = func_expr
        return parsed_expr
    elif isinstance(cons, exp.Anonymous):
        arguments = []
        for arg in cons.args['expressions']:
            if arg:
                parsed_expr = parse_expr(arg)
                arguments.append(parsed_expr)
        func_expr = WrapFunctionExpr()
        func_expr.func_name = cons.args['this']
        func_expr.arguments = arguments

        parsed_expr = WrapParsedExpr()
        parsed_expr.type = ParsedExprType.kFunction
        parsed_expr.function_expr = func_expr
        return parsed_expr
    elif isinstance(cons, exp.Func):
        arguments = []
        for arg in cons.args.values():
            if arg:
                parsed_expr = parse_expr(arg)
                arguments.append(parsed_expr)
        func_expr = WrapFunctionExpr()
        func_expr.func_name = cons.key
        func_expr.arguments = arguments

        parsed_expr = WrapParsedExpr()
        parsed_expr.type = ParsedExprType.kFunction
        parsed_expr.function_expr = func_expr
        return parsed_expr
    # in
    elif isinstance(cons, exp.In):
        left_operand = parse_expr(cons.args['this'])
        arguments = []
        for arg in cons.args['expressions']:
            if arg:
                arguments.append(parse_expr(arg))

        in_expr = WrapInExpr()
        in_expr.left = left_operand
        in_expr.arguments = arguments
        in_expr.in_type = True

        parsed_expr = WrapParsedExpr()
        parsed_expr.type = ParsedExprType.kIn
        parsed_expr.in_expr = in_expr

        return parsed_expr
    # not in
    elif isinstance(cons, exp.Not) and isinstance(cons.args['this'], exp.In):
        raw_in = cons.this
        left_operand = parse_expr(raw_in.args['this'])
        arguments = []
        for arg in raw_in.args['expressions']:
            if arg:
                arguments.append(parse_expr(arg))

        in_expr = WrapInExpr()
        in_expr.left = left_operand
        in_expr.arguments = arguments
        in_expr.in_type = False

        parsed_expr = WrapParsedExpr()
        parsed_expr.type = ParsedExprType.kIn
        parsed_expr.in_expr = in_expr

        return parsed_expr
    else:
        raise Exception(f"unknown condition type: {cons}")


def parse_expr(expr):
    try:
        return traverse_conditions(expr, parse_expr)
    except:
        if isinstance(expr, exp.Star):
            column_expr = WrapColumnExpr()
            column_expr.star = True
            parsed_expr = WrapParsedExpr(ParsedExprType.kColumn)
            parsed_expr.column_expr = column_expr
            return parsed_expr
        else:
            raise Exception(f"unknown expression type: {expr}")


def get_search_optional_filter_from_opt_params(opt_params: dict):
    optional_filter = None
    k_to_pop = []
    for k, v in opt_params.items():
        if k.lower() == "filter":
            if optional_filter is not None:
                raise InfinityException(ErrorCode.INVALID_EXPRESSION,
                                        "Only one filter is allowed in search optional parameters")
            if not isinstance(v, str):
                raise InfinityException(ErrorCode.INVALID_EXPRESSION,
                                        f"Invalid filter expression '{v}', type should be string, but get {type(v)}")
            optional_filter = traverse_conditions(condition(v))
            k_to_pop.append(k)
    for k in k_to_pop:
        opt_params.pop(k)
    return optional_filter


def get_local_constant_expr_from_python_value(value) -> WrapConstantExpr:
    # convert numpy types
    if isinstance(value, np.integer):
        value = int(value)
    elif isinstance(value, np.floating):
        value = float(value)
    elif isinstance(value, list) and isinstance(value[0], np.ndarray):
        if value[0].ndim <= 2:
            value = [x.tolist() for x in value]
        else:
            raise InfinityException(ErrorCode.INVALID_EXPRESSION,
                                    f"Invalid list member type: {type(value[0])}, ndarray dimension > 2")
    elif isinstance(value, np.ndarray):
        if value.ndim <= 2:
            value = value.tolist()
        else:
            raise InfinityException(ErrorCode.INVALID_EXPRESSION,
                                    f"Invalid list type: {type(value)}, ndarray dimension > 2")
    # match ConstantExpr
    constant_expression = WrapConstantExpr()
    match value:
        case str():
            constant_expression.literal_type = LiteralType.kString
            constant_expression.str_value = value
        case bool():
            constant_expression.literal_type = LiteralType.kBoolean
            constant_expression.bool_value = value
        case int():
            constant_expression.literal_type = LiteralType.kInteger
            constant_expression.i64_value = value
        case float():
            constant_expression.literal_type = LiteralType.kDouble
            constant_expression.f64_value = value
        case [int(), *_]:
            constant_expression.literal_type = LiteralType.kIntegerArray
            constant_expression.i64_array_value = value
        case [float(), *_]:
            constant_expression.literal_type = LiteralType.kDoubleArray
            constant_expression.f64_array_value = value
        case [[int(), *_], *_]:
            constant_expression.literal_type = LiteralType.kSubArrayArray
            constant_expression.i64_tensor_value = value
        case [[float(), *_], *_]:
            constant_expression.literal_type = LiteralType.kSubArrayArray
            constant_expression.f64_tensor_value = value
        case [[[int(), *_], *_], *_]:
            constant_expression.literal_type = LiteralType.kSubArrayArray
            constant_expression.i64_tensor_array_value = value
        case [[[float(), *_], *_], *_]:
            constant_expression.literal_type = LiteralType.kSubArrayArray
            constant_expression.f64_tensor_array_value = value
        case SparseVector([int(), *_] as indices, [int(), *_] as values):
            constant_expression.literal_type = LiteralType.kLongSparseArray
            constant_expression.i64_array_idx = indices
            constant_expression.i64_array_value = values
        case SparseVector([int(), *_] as indices, [float(), *_] as values):
            constant_expression.literal_type = LiteralType.kDoubleSparseArray
            constant_expression.i64_array_idx = indices
            constant_expression.f64_array_value = values
        case dict():
            if len(value) == 0:
                raise InfinityException(ErrorCode.INVALID_EXPRESSION, "Empty sparse vector")
            match next(iter(value.values())):
                case int():
                    constant_expression.literal_type = LiteralType.kLongSparseArray
                    constant_expression.i64_array_idx = [int(k) for k in value.keys()]
                    constant_expression.i64_array_value = [int(v) for v in value.values()]
                case float():
                    constant_expression.literal_type = LiteralType.kDoubleSparseArray
                    constant_expression.i64_array_idx = [int(k) for k in value.keys()]
                    constant_expression.f64_array_value = [float(v) for v in value.values()]
                case _:
                    raise InfinityException(ErrorCode.INVALID_EXPRESSION,
                                            f"Invalid sparse vector value type: {type(next(iter(value.values())))}")
        case Array():
            constant_expression.literal_type = LiteralType.kCurlyBracketsArray
            constant_expression.curly_brackets_array = [get_local_constant_expr_from_python_value(child) for child in
                                                        value.elements]
        case _:
            raise InfinityException(ErrorCode.INVALID_EXPRESSION, f"Invalid constant type: {type(value)}")
    return constant_expression


# invalid_name_array = [
#     [],
#     (),
#     {},
#     1,
#     1.1,
#     '',
#     ' ',
#     '12',
#     'name-12',
#     '12name',
#     '数据库名',
#     ''.join('x' for i in range(identifier_limit + 1)),
#     None,
# ]
identifier_limit = 65536


def check_valid_name(name, name_type: str = "Table"):
    if not isinstance(name, str):
        raise InfinityException(ErrorCode.INVALID_IDENTIFIER_NAME,
                                f"{name_type} name must be a string, got {type(name)}")
    if not re.match(r"^[a-zA-Z][a-zA-Z0-9_]*$", name):
        raise InfinityException(ErrorCode.INVALID_IDENTIFIER_NAME,
                                f"{name_type} name '{name}' is not valid. It should start with a letter and can contain only letters, numbers and underscores")
    if len(name) > identifier_limit:
        raise InfinityException(ErrorCode.INVALID_IDENTIFIER_NAME,
                                f"{name_type} name '{name}' is not of appropriate length")
    if name is None:
        raise InfinityException(ErrorCode.INVALID_IDENTIFIER_NAME, f"invalid name: {name}")
    if name.isspace():
        raise InfinityException(ErrorCode.INVALID_IDENTIFIER_NAME,
                                f"{name_type} name cannot be composed of whitespace characters only")
    if name == '':
        raise InfinityException(ErrorCode.INVALID_IDENTIFIER_NAME, f"invalid name: {name}")
    if name == ' ':
        raise InfinityException(ErrorCode.INVALID_IDENTIFIER_NAME, f"invalid name: {name}")
    if name.isdigit():
        raise InfinityException(ErrorCode.INVALID_IDENTIFIER_NAME, f"invalid name: {name}")


def name_validity_check(arg_name: str, name_type: str = "Table"):
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            if arg_name in kwargs:
                name = kwargs[arg_name]
            else:
                arg_names = list(inspect.signature(func).parameters.keys())
                name = args[arg_names.index(arg_name)]

            try:
                check_valid_name(name, name_type)
                return func(*args, **kwargs)
            except ValueError as e:
                raise

        return wrapper

    return decorator


def select_res_to_polars(res) -> (pl.DataFrame, Any):
    df_dict = {}
    data_dict, data_type_dict, extra_result = build_result(res)
    for k, v in data_dict.items():
        data_series = pd.Series(v, dtype=logic_type_to_dtype(data_type_dict[k]))
        df_dict[k] = data_series

    return pl.from_pandas(pd.DataFrame(df_dict))


def get_constant_expr(column_info):
    # process constant expression
    default = None
    if "default" in column_info:
        default = column_info["default"]

    if default is None:
        constant_expression = WrapConstantExpr()
        constant_expression.literal_type = LiteralType.kNull
        return constant_expression
    else:
        return get_local_constant_expr_from_python_value(default)


def get_constraints(column_info: dict) -> list[ConstraintType]:
    if column_info.get("constraints") is None:
        return []
    res = []
    constraints = column_info["constraints"]
    for constraint in constraints:
        constraint = constraint.lower()
        match constraint:
            case "null":
                if ConstraintType.kNull not in res:
                    res.append(ConstraintType.kNull)
                else:
                    raise InfinityException(ErrorCode.INVALID_CONSTRAINT_TYPE, f"Duplicated constraint: {constraint}")
            case "not null":
                if ConstraintType.kNotNull not in res:
                    res.append(ConstraintType.kNotNull)
                else:
                    raise InfinityException(ErrorCode.INVALID_CONSTRAINT_TYPE, f"Duplicated constraint: {constraint}")
            case "primary key":
                if ConstraintType.kPrimaryKey not in res:
                    res.append(ConstraintType.kPrimaryKey)
                else:
                    raise InfinityException(ErrorCode.INVALID_CONSTRAINT_TYPE, f"Duplicated constraint: {constraint}")
            case "unique":
                if ConstraintType.kUnique not in res:
                    res.append(ConstraintType.kUnique)
                else:
                    raise InfinityException(ErrorCode.INVALID_CONSTRAINT_TYPE, f"Duplicated constraint: {constraint}")
            case _:
                raise InfinityException(ErrorCode.INVALID_CONSTRAINT_TYPE, f"Unknown constraint: {constraint}")
    return res


def get_embedding_element_type(element_type):
    match element_type:
        case "bit":
            return EmbeddingDataType.kElemBit
        case "float32" | "float" | "f32":
            return EmbeddingDataType.kElemFloat
        case "float64" | "double" | "f64":
            return EmbeddingDataType.kElemDouble
        case "float16" | "f16":
            return EmbeddingDataType.kElemFloat16
        case "bfloat16" | "bf16":
            return EmbeddingDataType.kElemBFloat16
        case "uint8" | "u8":
            return EmbeddingDataType.kElemUInt8
        case "int8" | "i8":
            return EmbeddingDataType.kElemInt8
        case "int16" | "i16":
            return EmbeddingDataType.kElemInt16
        case "int32" | "int" | "i32":
            return EmbeddingDataType.kElemInt32
        case "int64" | "i64":
            return EmbeddingDataType.kElemInt64
        case _:
            raise InfinityException(ErrorCode.INVALID_EMBEDDING_DATA_TYPE, f"Unknown element type: {element_type}")


def get_embedding_type(column_big_info: list[str]) -> WrapDataType:
    column_type = WrapDataType()
    match column_big_info[0]:
        case "vector":
            column_type.logical_type = LogicalType.kEmbedding
        case "multivector":
            column_type.logical_type = LogicalType.kMultiVector
        case "tensor":
            column_type.logical_type = LogicalType.kTensor
        case "tensorarray":
            column_type.logical_type = LogicalType.kTensorArray
        case _:
            raise InfinityException(ErrorCode.INVALID_DATA_TYPE, f"Unknown data type: {column_big_info[0]}")

    length = column_big_info[1]
    element_type = column_big_info[2]

    embedding_type = WrapEmbeddingType()
    embedding_type.element_type = get_embedding_element_type(element_type)
    embedding_type.dimension = int(length)
    # physical_type = ttypes.PhysicalType()
    # physical_type.embedding_type = embedding_type
    column_type.embedding_type = embedding_type
    return column_type


def get_sparse_type(column_big_info: list[str]) -> WrapDataType:
    column_type = WrapDataType()
    assert column_big_info[0] == "sparse"
    column_type.logical_type = LogicalType.kSparse

    length = column_big_info[1]
    element_type = column_big_info[2]
    index_type = column_big_info[3]

    sparse_type = WrapSparseType()
    sparse_type.element_type = get_embedding_element_type(element_type)
    if index_type == "int8":
        sparse_type.index_type = EmbeddingDataType.kElemInt8
    elif index_type == "int16":
        sparse_type.index_type = EmbeddingDataType.kElemInt16
    elif index_type == "int32" or index_type == "int":
        sparse_type.index_type = EmbeddingDataType.kElemInt32
    elif index_type == "int64":
        sparse_type.index_type = EmbeddingDataType.kElemInt64
    else:
        raise InfinityException(ErrorCode.INVALID_EMBEDDING_DATA_TYPE, f"Unknown index type: {index_type}")
    sparse_type.dimension = int(length)

    column_type.sparse_type = sparse_type
    return column_type


def get_data_type(column_info: dict) -> WrapDataType:
    if "type" not in column_info:
        raise InfinityException(ErrorCode.NO_COLUMN_DEFINED, f"Column definition without data type")
    datatype = column_info["type"].lower()
    column_big_info = [item.strip() for item in datatype.split(",")]
    return get_data_type_from_column_big_info(column_big_info)


def get_data_type_from_column_big_info(column_big_info: list) -> WrapDataType:
    column_big_info_first_str = column_big_info[0]
    match column_big_info_first_str:
        case "vector" | "multivector" | "tensor" | "tensorarray":
            embedding_type = get_embedding_type(column_big_info)
            return embedding_type
            # return get_embedding_info(column_info, column_defs, column_name, index)
        case "sparse":
            sparse_type = get_sparse_type(column_big_info)
            return sparse_type
            # return get_sparse_info(column_info, column_defs, column_name, index)
        case "array":
            if len(column_big_info) < 2:
                raise InfinityException(ErrorCode.INVALID_DATA_TYPE, f"No element type for array!")
            proto_column_type = WrapDataType()
            proto_column_type.logical_type = LogicalType.kArray
            proto_column_type.array_type = get_data_type_from_column_big_info(column_big_info[1:])
            return proto_column_type
        case _:
            if len(column_big_info) > 1:
                raise InfinityException(ErrorCode.INVALID_DATA_TYPE,
                                        f"Unknown datatype: {column_big_info}, too many arguments")
            proto_column_type = WrapDataType()
            match column_big_info_first_str:
                case "int8":
                    proto_column_type.logical_type = LogicalType.kTinyInt
                case "int16":
                    proto_column_type.logical_type = LogicalType.kSmallInt
                case "integer" | "int32" | "int":
                    proto_column_type.logical_type = LogicalType.kInteger
                case "int64":
                    proto_column_type.logical_type = LogicalType.kBigInt
                case "int128":
                    proto_column_type.logical_type = LogicalType.kHugeInt
                case "float" | "float32":
                    proto_column_type.logical_type = LogicalType.kFloat
                case "double" | "float64":
                    proto_column_type.logical_type = LogicalType.kDouble
                case "float16":
                    proto_column_type.logical_type = LogicalType.kFloat16
                case "bfloat16":
                    proto_column_type.logical_type = LogicalType.kBFloat16
                case "varchar":
                    proto_column_type.logical_type = LogicalType.kVarchar
                case "bool":
                    proto_column_type.logical_type = LogicalType.kBoolean
                case "date":
                    proto_column_type.logical_type = LogicalType.kDate
                case "time":
                    proto_column_type.logical_type = LogicalType.kTime
                case "datetime":
                    proto_column_type.logical_type = LogicalType.kDateTime
                case "timestamp":
                    proto_column_type.logical_type = LogicalType.kTimestamp
                case _:
                    raise InfinityException(ErrorCode.INVALID_DATA_TYPE, f"Unknown datatype: {column_big_info_first_str}")
            return proto_column_type


def get_ordinary_info(column_info_, column_defs, column_name, index):
    # "c1": {"type": "int", "constraints":["primary key", ...], "default": 1/"asdf"/[1,2]/...}
    # process column definition

    proto_column_def = WrapColumnDef()
    proto_column_def.id = index
    proto_column_def.column_name = column_name

    column_info = {}
    for key, value in column_info_.items():
        column_info[key.lower()] = value

    proto_column_def.column_type = get_data_type(column_info)
    proto_column_def.constant_expr = get_constant_expr(column_info)
    proto_column_def.constraints = get_constraints(column_info)
    if "comment" in column_info:
        proto_column_def.comment = column_info["comment"]
    column_defs.append(proto_column_def)
