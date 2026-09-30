# MrWu｜云聪视频理解与共通音视频基建

日期：2026-09-30。此目录是 MrWu 部分的新交付，保存指定课程的理解与共通工具发布；没有改写现有交易策略。

## 阅读入口

1. [云聪五段视频完整理解总结](云聪五段视频_完整理解总结_20260930.md)：体系、13 类买点、逐段笔记、原片时间及未定义条件。
2. [基建改进与验收](本地音视频基建_共通化更新说明_20260930.md)：实际改动、已有能力、11 项测试和真实 GPU 验证。
3. [跨项目调用指南](跨项目音视频识别调用指南.md)：安装、配置、提取、画面复核与证据读取。
4. [共用源码](shared_media_platform/)：可独立安装，含构建配置、核心、测试和兼容桥示例。
5. [安装包](local_media_platform-2.1.0-py3-none-any.whl)：Python wheel，依赖按需安装。

## Google Drive

- [课程理解交付目录](https://drive.google.com/drive/folders/1zVOP1LtxWf4HQjA4Eynawlktge9F2eUc)
- [跨项目共通音视频基建](https://drive.google.com/drive/folders/1vk4nIGxQoSZtqCP-dNqpti7fqwnbgRNg)

## 安装

本目录下执行：

```powershell
python -m pip install ".\local_media_platform-2.1.0-py3-none-any.whl[asr,ocr,gpu]"
python -m media_platform doctor
```

也可以进入 `shared_media_platform` 后 `python -m pip install ".[asr,ocr,gpu]"`。FFmpeg/ffprobe 需在 PATH。共用包并未发布到 PyPI。

## 范围

课程图形和经验判断未被统计回测。本目录不附原视频、完整逐字稿、截图、个人互动材料、模型权重或认证信息。原素材以总结中的文件名、SHA-256 和时间定位。`SHA256SUMS.json` 校验交付文件。
