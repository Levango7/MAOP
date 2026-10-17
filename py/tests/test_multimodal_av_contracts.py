"""`multimodal_av_contracts` 的契约测试 —— 个人版侧唯一能守住 MAOS 边界的证据。

这个模块自己没有任何实现（ADR-016 D5：Whisper / CLAP / ffmpeg 归私有仓 MAOS），
它只声明"一个音频/视频 provider 必须长什么样"以及"两个内置空间怎么登记"。
正因为它是跨仓接口，它错了不会被本仓的运行路径发现 —— 只会在对面注入 provider 时炸。

两条 2026-10-18 实测出来的事实，是本文件存在的理由：

1. **`@runtime_checkable` 的 isinstance 查的是"成员是否都在对象上"，不是"抽象方法是否实现"。**
   本 Protocol 里 `is_semantic` 与 `embed` 带默认实现，但它们仍然是 Protocol 成员 ——
   所以一个鸭子类型的 provider 若不自己写这两个名字，`isinstance()` 直接判否；
   反过来，**继承** Protocol 才能白拿默认实现。这个不对称对 MAOS 是隐形要求，必须钉住。
2. **本模块 docstring 原先声称 MAOS 已提供 `WhisperAudioProvider` / `ClapAudioProvider` /
   `FramePoolVideoProvider`。实测 MAOS tip `1a19c7f` 三个类名、`embed_audio`、`embed_video`
   以及对本模块的 import 全部 0 命中** ⇒ 对面还没落地，措辞已按事实降级（见模块 docstring）。

泄漏防护：注册表 `_SPACES` 是进程级全局，夹具前后各 `reset_multimodal_spaces()` 一次。
"""

from __future__ import annotations

import ast
from dataclasses import replace
from pathlib import Path

import pytest

import maop.core.memory.multimodal_av_contracts as av
from maop.core.memory.multimodal_embed import MULTIMODAL_DIM
from maop.core.memory.multimodal_spaces import (
    get_multimodal_space,
    register_multimodal_space,
    reset_multimodal_spaces,
)


@pytest.fixture(autouse=True)
def _isolate_space_registry():
    reset_multimodal_spaces()
    yield
    reset_multimodal_spaces()


class FullAudio:
    """鸭子类型且**补齐全部 Protocol 成员**的音频 provider（含带默认值的那两个）。"""

    def __init__(self) -> None:
        self.audio_calls: list[object] = []

    @property
    def provider_id(self) -> str:
        return "duck-audio"

    @property
    def dimension(self) -> int:
        return av.AUDIO_SPACE_DIM

    @property
    def is_semantic(self) -> bool:
        return True

    def embed_audio(self, source, *, sr: int = 16_000) -> list[float]:
        self.audio_calls.append(source)
        return [1.0] * self.dimension

    def embed(self, source) -> list[float]:
        return self.embed_audio(source)


class FullVideo:
    def __init__(self) -> None:
        self.video_calls: list[object] = []

    @property
    def provider_id(self) -> str:
        return "duck-video"

    @property
    def dimension(self) -> int:
        return av.VIDEO_SPACE_DIM

    @property
    def is_semantic(self) -> bool:
        return True

    def embed_video(self, source, *, frames: int = 8, fps: float = 1.0) -> list[float]:
        self.video_calls.append(source)
        return [2.0] * self.dimension

    def embed(self, source) -> list[float]:
        return self.embed_video(source)


class InheritingAudio(av.AudioEmbeddingProvider):
    """只实现抽象成员、靠继承白拿 `embed()` / `is_semantic` 默认实现的 provider。"""

    def __init__(self) -> None:
        self.audio_calls: list[object] = []

    @property
    def provider_id(self) -> str:
        return "inherit-audio"

    @property
    def dimension(self) -> int:
        return av.AUDIO_SPACE_DIM

    def embed_audio(self, source, *, sr: int = 16_000) -> list[float]:
        self.audio_calls.append(source)
        return [1.0] * self.dimension


class InheritingVideo(av.VideoEmbeddingProvider):
    @property
    def provider_id(self) -> str:
        return "inherit-video"

    @property
    def dimension(self) -> int:
        return av.VIDEO_SPACE_DIM

    def embed_video(self, source, *, frames: int = 8, fps: float = 1.0) -> list[float]:
        return [2.0] * self.dimension


# ── 空间口径（对面按这两个宽度建表/建向量列） ──────────────────────────


def test_space_dims_and_names_are_the_cross_repo_contract() -> None:
    assert av.AUDIO_SPACE_DIM == 768, "Whisper small/medium 编码器宽度，改了 MAOS 会对不上"
    assert av.AUDIO_SPACE_NAME == "audio-768"
    assert av.VIDEO_SPACE_DIM == MULTIMODAL_DIM == 512, "视频空间必须复用 CLIP 宽度"
    assert av.VIDEO_SPACE_NAME == f"video-{MULTIMODAL_DIM}"
    assert (av.MODALITY_AUDIO, av.MODALITY_VIDEO) == ("audio", "video")


def test_register_builtin_av_spaces_is_idempotent() -> None:
    """同一份定义重复登记是 no-op（空间模块按 dataclass 相等判定），不是异常。"""
    first = av.register_builtin_av_spaces()
    again = av.register_builtin_av_spaces()
    assert [s.name for s in first] == ["audio-768", "video-512"]
    assert all(a == b for a, b in zip(first, again, strict=True))
    assert av.get_audio_space() == first[0]
    assert av.get_video_space() == first[1]


def test_av_spaces_use_distinct_tables_from_default_space() -> None:
    """两个空间与默认空间同库共存时表名必须互不相同，否则会静默共享行。"""
    av.register_builtin_av_spaces()
    tables = {
        get_multimodal_space().table_name,
        av.get_audio_space().table_name,
        av.get_video_space().table_name,
    }
    assert len(tables) == 3, tables
    assert av.get_audio_space().modalities == ("audio",)
    assert av.get_video_space().modalities == ("video",)


def test_redefining_a_registered_space_name_raises_not_silently_overwrites() -> None:
    av.register_builtin_av_spaces()
    conflicting = replace(av.get_audio_space(), dimension=av.AUDIO_SPACE_DIM + 64)
    with pytest.raises(ValueError, match="already registered with a different definition"):
        register_multimodal_space(conflicting)


# ── 协议形状：把 isinstance 的真实语义钉住（对 MAOS 是隐形要求） ────────


def test_duck_typed_provider_with_full_surface_is_accepted() -> None:
    assert isinstance(FullAudio(), av.AudioEmbeddingProvider)
    assert isinstance(FullVideo(), av.VideoEmbeddingProvider)


def test_subclassing_the_protocol_is_accepted_and_inherits_defaults() -> None:
    assert isinstance(InheritingAudio(), av.AudioEmbeddingProvider)
    assert isinstance(InheritingVideo(), av.VideoEmbeddingProvider)


def test_partial_duck_typed_provider_is_rejected() -> None:
    """缺任意 Protocol 成员（哪怕那个成员在 Protocol 里有默认实现）都判否。

    这就是 MAOS 侧最容易踩的地方：只写 `embed_audio` 的鸭子类型 provider
    会被 `isinstance()` 拒掉，而不是被"少写抽象方法"的报错提醒。
    """

    class _AudioOnlyAbstraction:
        @property
        def provider_id(self) -> str:
            return "half-baked"

        @property
        def dimension(self) -> int:
            return 8

        def embed_audio(self, source, *, sr: int = 16_000) -> list[float]:
            return [0.0] * self.dimension

    assert not isinstance(_AudioOnlyAbstraction(), av.AudioEmbeddingProvider)
    assert not isinstance(_AudioOnlyAbstraction(), av.VideoEmbeddingProvider)


def test_embed_defaults_only_exist_when_subclassing() -> None:
    """`embed()` 的默认实现来自继承 Protocol，鸭子类型不白送。"""

    class _DuckNoEmbed:
        provider_id = "duck"
        dimension = 8
        is_semantic = True

        def embed_audio(self, source, *, sr: int = 16_000) -> list[float]:
            return [0.0] * self.dimension

    assert not hasattr(_DuckNoEmbed(), "embed"), "鸭子类型不该凭空拿到 Protocol 的默认入口"
    assert hasattr(InheritingAudio(), "embed"), "继承 Protocol 才有默认入口"
    assert InheritingAudio().is_semantic is True


def test_audio_embed_text_source_degrades_to_zero_vector_of_right_width() -> None:
    """文本源不该送去真编码，也不许抛，更不许给错宽度（写路径会按 dimension 校验）。"""
    p = InheritingAudio()
    out = p.embed("plain text, not a file")
    assert out == [0.0] * av.AUDIO_SPACE_DIM, "降级必须是零向量而非异常/错宽"
    assert p.audio_calls == [], "文本源不该被送去真编码"

    clipped = p.embed(Path("clip.wav"))
    assert clipped == [1.0] * av.AUDIO_SPACE_DIM
    assert p.audio_calls == [Path("clip.wav")]


def test_video_embed_dispatch_by_source_type() -> None:
    class _V(InheritingVideo):
        def __init__(self) -> None:
            self.video_calls: list[object] = []

        def embed_video(self, source, *, frames: int = 8, fps: float = 1.0) -> list[float]:
            self.video_calls.append(source)
            return [2.0] * self.dimension

    v = _V()
    assert v.embed(b"\x00\x01") == [0.0] * av.VIDEO_SPACE_DIM, "bytes 不该被当路径"
    assert v.video_calls == []
    assert v.embed("movie.mp4") == [2.0] * av.VIDEO_SPACE_DIM
    assert v.video_calls == ["movie.mp4"]


# ── 边界守卫：这里不许长出肌肉（只查 import，散文允许提名字） ──────────


def test_module_imports_stay_inside_the_personal_edition_boundary() -> None:
    """ADR-016 D5：实现归 MAOS。按 **AST 导入** 判定，不按全文搜词 ——

    docstring 本来就必须写 Whisper/CLAP/ffmpeg 来说明边界在哪，
    拿散文当输入会把"讲边界"误判成"越界"（本文件第一版就是这么错的）。
    """
    tree = ast.parse(Path(av.__file__).read_text(encoding="utf-8"))
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            imported.append(node.module)

    forbidden_roots = ("whisper", "clap", "ffmpeg", "torchaudio", "librosa", "cv2")
    leaked = [m for m in imported if m.split(".")[0] in forbidden_roots]
    assert not leaked, f"个人版契约层 import 了企业版实现依赖：{leaked}"

    enterprise = [m for m in imported if m.startswith("maop.enterprise")]
    assert not enterprise, f"个人版 core 反向依赖 MAOS：{enterprise}"
