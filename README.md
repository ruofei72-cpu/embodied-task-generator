# Embodied Task Generator · 具身智能任务标准化 Skill

将表格里的任务变体整理成统一的具身智能任务定义，帮助采集、标注和运营人员减少重复整理工作。

本项目提供 **Skill 指令、标准化提示词、字段配置、示例和本地校验工具**。任务内容由 AI 助手结合输入生成，再经过规则校验和人工审核；当前没有独立的模型调用、Excel 批处理或平台上传程序。

## 业务场景

同一类任务可能有不同数量、物品和位置的变体。本 Skill 将每条变体整理为任务描述、初始状态、可验证的目标、泛化方向、操作能力、环境、标签和 SOP。

例如“从储物箱取出两盒感冒药，放到 A 货架左侧”，输出需要保留“两盒”“感冒药”“A 货架左侧”等条件。初始状态描述执行前的世界，目标描述完成后的状态，SOP 描述执行过程。缺少关键条件时先确认，不将猜测填成事实。

## 快速使用

### 在 AI 助手中使用

1. 提供 [SKILL.md](SKILL.md)、[提示词](variant-standardizer.txt)、[字段配置](config.example.json) 和任务表格。支持安装 Skill 的助手也可以将整个仓库作为 Skill 文件夹加载。
2. 参考 [输入示例](input_example.csv)，每行一个变体；`Variant Instruction` 必填。同一任务组的 ID 和名称允许向下继承，新 ID 必须提供名称。
3. 先检查输入，信息充分时生成 JSON 任务数组；缺少关键条件时先列出待确认项。
4. 校验输出并人工审核，再按目标平台字段要求整理导入文件。

示例请求：

```text
请按 SKILL.md、variant-standardizer.txt 和 config.example.json 整理 input_example.csv。
每条变体独立生成一个任务，保留数量、物品和位置条件。
信息充分时，输出符合字段约定的 JSON 数组并保存为 tasks.json。
信息不足或有重复候选时，先列出问题让我确认。
```

### 在本地校验

需要 Python 3.10 或更新版本，仅使用标准库，无需安装第三方包。

```bash
python3 scripts/validate_tasks.py output_example.json
python3 scripts/validate_tasks.py output_example.csv
python3 scripts/validate_tasks.py tasks.json --config config.example.json
python3 -m unittest discover -s tests -v
```

成功退出码为 `0`，失败为 `1`。输入路径按当前工作目录解析，默认配置从脚本所在仓库读取。命令只校验文件，不改写输入、不调用模型、不上传数据。

## 数据约定（v1.1）

[config.example.json](config.example.json) 统一维护环境、任务类型、灵巧度和用时档位的允许值。提示词、示例和工具使用同一份配置。

JSON 顶层为非空数组，每个任务包含以下字段，不接受额外字段：

| 字段 | 类型与含义 |
| --- | --- |
| `task_name` | 非空字符串，原任务名称加变体编号，如 `Restock Medicine-v01` |
| `description` | 非空字符串，原始变体指令；可移除 `variant_01:` 这类元数据前缀 |
| `initial_state` | 非空字符串，开始执行前可观察的状态 |
| `task_goal` | 非空字符串数组，可验证的完成条件，保留输入的数量、物品和位置等限制 |
| `generalization_axes` | 非空字符串，分号分隔的泛化维度 |
| `task_type` | 配置中的操作能力枚举 |
| `dexterity_level` | 配置中的灵巧度枚举 |
| `environment` | 配置中的环境枚举；无法确定时先复核，不默认归到零售 |
| `estimated_duration` | 配置中的用时档位，属于估计值，需人工复核 |
| `tool_use` / `fine_tool_use` | JSON 布尔值 `true` 或 `false`，不可写成字符串 |
| `sop` | 非空字符串，有顺序的可执行物理动作 |
| `project` | 非空字符串，使用提供的项目标记，否则使用配置默认值 |

完整示例见 [output_example.json](output_example.json)。

### CSV 导入映射

| CSV 列 | JSON 来源 |
| --- | --- |
| `Task Name` | `task_name` |
| `Description` | `description` |
| `Task Goal` | `task_goal` 序列化为 JSON 数组 |
| `Initial State` | `initial_state` |
| `Generalization Axes` | `generalization_axes` |
| `Task Type` | `task_type` |
| `Environment` | `environment` |
| `Label` | JSON 对象：`tool_use`、`estimated_duration`、`dexterity_level`、`fine_tool_use`、`project` |
| `SOP` | `sop` |

CSV 是文本格式，但 `Label` 单元格里的 JSON 仍使用布尔值。引用和转义方式见 [output_example.csv](output_example.csv)。本项目使用示范导入格式，实际平台可能要求不同的列名、标签和枚举，需确认映射后使用。

相较原示例，v1.1 将 `"false"` 修正为 `false`，将 `≤4min` 统一到 `2–5min` 档位，增加 `SOP` 列以保留操作说明。原先只接受 8 列的平台需要单独确认 SOP 的接收方式。

## 校验范围与人工审核

工具检查结构、必填字段、枚举、布尔类型、工具标签逻辑、重名和规范化描述的完全重复。完全重复仅忽略大小写和空白差异；工具报错但不会删除任务。

工具无法确认物理可行性、真实用时、语义相似度、输入限制是否完整保留或平台是否接受文件。高相似任务和生成内容仍需人工审核。配置不包含未经实现的相似度阈值，不自动删除近似任务。

## 仓库结构

```text
embodied-task-generator/
├── README.md
├── SKILL.md
├── config.example.json
├── variant-standardizer.txt
├── input_example.csv
├── output_example.json
├── output_example.csv
├── scripts/validate_tasks.py
├── tests/test_validate_tasks.py
└── .github/workflows/validate.yml
```

项目使用脱敏示例展示工作流，不包含公司内部平台、账号或生产数据。原 README 的 MIT 授权声明沿用；仓库目前未附独立 LICENSE 文件。
