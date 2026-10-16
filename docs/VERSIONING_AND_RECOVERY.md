# 岜团村研究版本管理与恢复

更新日期：2026年10月16日。

90分钟联合保护阶段：开发前回退标签 `batuan-before-90min-search-20261016`（`f472e25`），开发分支 `codex/batuan-90min-robust-search-20261016`，独立Git备份 `../PyFireCA-backups/batuan-before-90min-search-20261016/repository.bundle` 已验证。三类线形、90分钟民居/桥联合目标及独立5%敏感性试验见[本轮说明](BATUAN_PROTECTION90_SEARCH.md)。下方保留先前阶段的完成记录。

最新迭代阶段：开发前回退标签 `batuan-before-adaptive-search-20261016`（`a85f601`），新分支 `codex/batuan-adaptive-firebreak-20261016`。独立Git备份位于 `/Users/tone/Documents/github/PyFireCA-backups/batuan-before-adaptive-search-20261016/repository.bundle`，已验证。原24候选结果和原模拟器保留。详见[本轮算法与恢复说明](BATUAN_ADAPTIVE_FIREBREAK_SEARCH.md)。下表为初次研究计划阶段的历史保存点。

本轮完成标签 `batuan-adaptive-20261016`，新的结果和代码备份为 `/Users/tone/Documents/github/PyFireCA-backups/batuan-adaptive-20261016`。新归档包含先导训练缓存、正式研究结果、全图回放及源码/渲染快照；基础影像和空间资产继续使用原有验证备份。研究的参数一致恢复已验证，搜索/几何/留出/回放结果相同，0新增CA求解。

现有模拟器先保存为可检出的代码基线，研究计划另行提交。大型本地影像与结果保持在Git之外，使用压缩备份和逐文件SHA256清单保存。恢复时优先使用独立目录，保留当前工作区与历史结果。

## 1 当前版本

| 项目 | 已保存内容 |
|---|---|
| 原分支 | `main`，保留在`84e2765` |
| 当前开发分支 | `codex/village-research-plan-20261016` |
| 模拟器基线提交 | `f6c163220f2530cbefca366b8afc82a492492872` |
| 模拟器基线标签 | `batuan-simulator-20261016`，包含本轮计划开始前的51个变更文件 |
| 研究计划标签 | `village-research-plan-20261016`，对应本计划和恢复文档的独立提交 |
| 基线测试 | `.venv/bin/python -m pytest -q`，1021 passed，9条依赖弃用警告，89.38秒 |
| 当前可运行案例 | `runs/batuan-slope-contact-20261016` |
| 历史中心高程对照 | `runs/batuan-contact-20261016` |

上述标签标识代码和演示案例的版本，不代表燃烧参数经过实测校准。测试计数只对应本次实际运行，不承诺其他环境已经验证。

## 2 本地备份

目录：`/Users/tone/Documents/github/PyFireCA-backups/batuan-20261016-f6c1632`。

| 文件 | 用途 |
|---|---|
| `repository.bundle` | 本次完成后保存的离线Git历史、开发分支及两个标签 |
| `batuan-data.tar.gz` | 当前岜团村输入和选定运行结果，276458308字节，约264 MiB |
| `data-manifest.json` | 834个文件的项目相对路径、大小与SHA256 |
| `backup-validation.json` | 压缩包校验与全部成员逐文件核验结果 |
| `environment.json` | Python、平台与已安装包版本；不包含虚拟环境二进制副本 |
| `SHA256SUMS` | 备份文件的整体校验值 |

数据压缩包SHA256：`49e3c145396df07fc2beed67cb8e871ea71815d5085a7dd19fdf3666bfc34779`。

备份范围包括：岜团村拼接影像目录和人工LabelMe标注、使用的FABDEM瓦片、旧接触版案例及其目录、接触几何审计、新版case/run/spatial/buildings和摘要、已经完成的60分钟交互结果`bd9190cd7eaa41489c5772db56759dc5`及截图。新目录中以后生成的交互作业不会自动进入这份备份。其他高友村历史结果、桌面上的原始下载分块和整个虚拟环境不在本次数据备份范围。

备份位于同一台电脑，已核验可读性；需要异机容灾时，将整个备份目录另行复制到外部介质。当前没有推送远程仓库。

## 3 查看和回到代码基线

查看保存点：

```bash
cd /Users/tone/Documents/github/PyFireCA
git log --oneline --decorate -5
git show --stat batuan-simulator-20261016
git show --stat village-research-plan-20261016
```

在独立目录检出旧代码，保留当前分支和文件：

```bash
git worktree add --detach ../PyFireCA-batuan-recovery batuan-simulator-20261016
```

目录必须尚不存在。需要在旧版上继续修改时，在新worktree创建新的`codex/`分支；原标签不移动。

复用现有本地数据和依赖，在独立端口运行旧代码：

```bash
cd /Users/tone/Documents/github/PyFireCA-batuan-recovery
PYTHONPATH="$PWD/src" /Users/tone/Documents/github/PyFireCA/.venv/bin/python \
  scripts/start_batuan_simulation.py \
  --prepared /Users/tone/Documents/github/PyFireCA/runs/batuan-slope-contact-20261016 \
  --port 8853 --no-browser
```

`PYTHONPATH`确保使用新worktree中的旧源码，避免现有虚拟环境的editable安装仍导入当前开发代码。打开`http://127.0.0.1:8853/`核对；原8851服务不用停止。不要使用`git reset --hard`或`git clean`作为常规恢复方法。

## 4 恢复影像与运行数据

先核对备份整体：

```bash
cd /Users/tone/Documents/github/PyFireCA-backups/batuan-20261016-f6c1632
shasum -a 256 -c SHA256SUMS
git bundle verify repository.bundle
```

恢复到一个新建的空目录，例如`/Users/tone/Documents/github/PyFireCA-data-restore`，先核对内容再决定替换损坏文件：

```bash
mkdir /Users/tone/Documents/github/PyFireCA-data-restore
tar -xzf batuan-data.tar.gz -C /Users/tone/Documents/github/PyFireCA-data-restore
```

逐文件验证：

```bash
/Users/tone/Documents/github/PyFireCA/.venv/bin/python - <<'PY'
import hashlib
import json
from pathlib import Path

backup = Path('/Users/tone/Documents/github/PyFireCA-backups/batuan-20261016-f6c1632')
restored = Path('/Users/tone/Documents/github/PyFireCA-data-restore')
manifest = json.loads((backup / 'data-manifest.json').read_text())
for item in manifest['files']:
    path = restored / item['path']
    assert path.stat().st_size == item['bytes'], path
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    assert digest.hexdigest() == item['sha256'], path
print(f"Verified {len(manifest['files'])} files")
PY
```

现有case元数据中包含原项目的绝对路径。临时目录恢复用于验证内容；恢复回原项目的相同相对路径后可继续使用原记录。若希望整套程序迁移到不同目录，需要重建案例或明确重定位空间资产和来源路径，不能仅解压后宣称迁移已完成。替换前将原文件另存，不覆盖尚未确认的较新结果。

## 5 从离线Git备份恢复仓库

```bash
git clone /Users/tone/Documents/github/PyFireCA-backups/batuan-20261016-f6c1632/repository.bundle \
  /Users/tone/Documents/github/PyFireCA-code-restore
git -C /Users/tone/Documents/github/PyFireCA-code-restore switch --detach batuan-simulator-20261016
```

Git备份与数据压缩包分别恢复；依赖可按`environment.json`核对，项目安装要求以对应版本的`pyproject.toml`为准。

## 6 后续提交规则

1. 每个可运行任务单独提交，代码、配置、测试和对应文档一起保存。
2. 传播规则、参数、分类模型和优化方法分别记录版本；模型变化后重算方案，缓存键包含代码和参数版本。
3. 输入、case和run采用新目录及ID；保留来源哈希，不覆盖旧实验或让优化器直接改基础材质数组。
4. 重要里程碑使用新的不可变标签；提交前运行与变更相称的验证并记录实际结果。
5. 需要远程共享时单独执行推送；本次只做本地提交、标签和备份。
