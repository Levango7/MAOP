"""Tests for MAOP.core.output_parser and schema gate in maop_verify."""

from __future__ import annotations

from maop.core.reliability.error_schema import new_result
from maop.core.agent.llm_chat.output_parser import OutputParser
from maop.maop_verify import GATE_REGISTRY, VerifyEngine

# ── OutputParser: extract_json ──────────────────────────────────


class TestExtractJsonCodeBlock:
    def test_json_code_block(self):
        parser = OutputParser()
        text = '```json\n{"x": 1, "y": 2}\n```'
        pr = parser.extract_json(text)
        assert pr.success is True
        assert pr.data == {"x": 1, "y": 2}
        assert pr.format == "code_block"

    def test_plain_code_block(self):
        parser = OutputParser()
        text = '```\n{"a": "b"}\n```'
        pr = parser.extract_json(text)
        assert pr.success is True
        assert pr.data == {"a": "b"}

    def test_code_block_with_surrounding_text(self):
        parser = OutputParser()
        text = 'Here is the result:\n```json\n{"result": 42}\n```\nDone.'
        pr = parser.extract_json(text)
        assert pr.success is True
        assert pr.data == {"result": 42}


class TestExtractJsonRaw:
    def test_raw_json_object(self):
        parser = OutputParser()
        pr = parser.extract_json('{"key": "value"}')
        assert pr.success is True
        assert pr.data == {"key": "value"}
        assert pr.format == "raw"

    def test_raw_json_array(self):
        parser = OutputParser()
        pr = parser.extract_json('[1, 2, 3]')
        assert pr.success is True
        assert pr.data == [1, 2, 3]


class TestExtractJsonEmbedded:
    def test_embedded_json(self):
        parser = OutputParser()
        text = 'The answer is {"x": 1} as shown above.'
        pr = parser.extract_json(text)
        assert pr.success is True
        assert pr.data == {"x": 1}
        assert pr.format == "embedded"

    def test_no_json(self):
        parser = OutputParser()
        pr = parser.extract_json("Just plain text here.")
        assert pr.success is False

    def test_empty_text(self):
        parser = OutputParser()
        pr = parser.extract_json("")
        assert pr.success is False

    def test_nested_json(self):
        parser = OutputParser()
        text = '{"outer": {"inner": 42}}'
        pr = parser.extract_json(text)
        assert pr.success is True
        assert pr.data == {"outer": {"inner": 42}}


class TestExtractCodeBlocks:
    def test_all_code_blocks(self):
        parser = OutputParser()
        text = '```python\nprint("hi")\n```\n```json\n{"x":1}\n```'
        blocks = parser.extract_code_blocks(text)
        assert len(blocks) == 2

    def test_filtered_by_language(self):
        parser = OutputParser()
        text = '```python\nprint("hi")\n```\n```json\n{"x":1}\n```'
        blocks = parser.extract_code_blocks(text, language="json")
        assert len(blocks) == 1
        assert '"x":1' in blocks[0]


class TestExtractFunctionResults:
    def test_function_result_tag(self):
        parser = OutputParser()
        text = '<function_result>{"success": true, "output": "hello"}</function_result>'
        results = parser.extract_function_results(text)
        assert len(results) == 1
        assert results[0].success is True

    def test_function_result_prefix(self):
        parser = OutputParser()
        text = 'Function result: {"success": true}'
        results = parser.extract_function_results(text)
        assert len(results) == 1

    def test_no_function_results(self):
        parser = OutputParser()
        results = parser.extract_function_results("plain text")
        assert results == []


# ── OutputParser: validate ──────────────────────────────────────


class TestValidate:
    def test_validate_dict(self):
        from pydantic import BaseModel
        parser = OutputParser()

        class MyModel(BaseModel):
            name: str
            age: int

        instance, error = parser.validate({"name": "Alice", "age": 30}, MyModel)
        assert instance is not None
        assert error == ""
        assert instance.name == "Alice"

    def test_validate_failure(self):
        from pydantic import BaseModel
        parser = OutputParser()

        class MyModel(BaseModel):
            name: str
            age: int

        instance, error = parser.validate({"name": "Alice"}, MyModel)
        assert instance is None
        assert error != ""

    def test_validate_none(self):
        from pydantic import BaseModel
        parser = OutputParser()

        class MyModel(BaseModel):
            x: int

        instance, error = parser.validate(None, MyModel)
        assert instance is None
        assert "No data" in error


class TestExtractAndValidate:
    def test_extract_and_validate(self):
        from pydantic import BaseModel
        parser = OutputParser()

        class Result(BaseModel):
            status: str
            count: int

        text = '```json\n{"status": "ok", "count": 5}\n```'
        instance, pr = parser.extract_and_validate(text, Result)
        assert instance is not None
        assert instance.status == "ok"
        assert pr.success is True

    def test_extract_and_validate_failure(self):
        from pydantic import BaseModel
        parser = OutputParser()

        class Result(BaseModel):
            status: str
            count: int

        text = "no json here"
        instance, pr = parser.extract_and_validate(text, Result)
        assert instance is None
        assert pr.success is False


# ── Schema gate in VerifyEngine ─────────────────────────────────


class TestSchemaGate:
    def test_schema_gate_registered(self):
        assert "schema" in GATE_REGISTRY

    def test_schema_gate_pass_with_structured_output(self):
        engine = VerifyEngine()
        result = new_result(
            agent="test", task="test",
            structured_output={"name": "Alice", "age": 30},
        )
        plan = {
            "gates": ["schema"],
            "expected_schema": {
                "type": "object",
                "required": ["name"],
                "properties": {"name": {"type": "string"}, "age": {"type": "integer"}},
            },
        }
        vr = engine.verify(plan, result)
        assert vr.passed is True

    def test_schema_gate_fail_missing_required(self):
        engine = VerifyEngine()
        result = new_result(
            agent="test", task="test",
            structured_output={"age": 30},
        )
        plan = {
            "gates": ["schema"],
            "expected_schema": {
                "type": "object",
                "required": ["name"],
            },
        }
        vr = engine.verify(plan, result)
        assert vr.passed is False
        assert "Missing required field: name" in vr.feedback

    def test_schema_gate_fail_type_mismatch(self):
        engine = VerifyEngine()
        result = new_result(
            agent="test", task="test",
            structured_output={"age": "not_a_number"},
        )
        plan = {
            "gates": ["schema"],
            "expected_schema": {
                "type": "object",
                "properties": {"age": {"type": "integer"}},
            },
        }
        vr = engine.verify(plan, result)
        assert vr.passed is False
        assert "Type mismatch" in vr.feedback

    def test_schema_gate_pass_no_expected(self):
        engine = VerifyEngine()
        result = new_result(agent="test", task="test", stdout="anything")
        plan = {"gates": ["schema"]}
        vr = engine.verify(plan, result)
        assert vr.passed is True

    def test_schema_gate_parse_stdout(self):
        engine = VerifyEngine()
        result = new_result(agent="test", task="test", stdout='{"name": "Bob"}')
        plan = {
            "gates": ["schema"],
            "expected_schema": {
                "type": "object",
                "required": ["name"],
                "properties": {"name": {"type": "string"}},
            },
        }
        vr = engine.verify(plan, result)
        assert vr.passed is True


# ── MaopResult structured_output ─────────────────────────────────


class TestMaopResultStructuredOutput:
    def test_structured_output_field(self):
        r = new_result(
            agent="test", task="test",
            structured_output={"key": "value"},
        )
        assert r.structured_output == {"key": "value"}

    def test_structured_output_default_none(self):
        r = new_result(agent="test", task="test")
        assert r.structured_output is None
