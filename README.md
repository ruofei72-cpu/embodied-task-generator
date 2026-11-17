# Embodied Task Generator

A configurable Python tool that turns structured atomic tasks into validated task variations and spreadsheet-ready collection instructions. Core generation runs offline, without an LLM API or simulator.

具身数据采集任务泛化工具：用配置定义变化维度，通过代码控制变体数量、组合、校验与导出。原始 Prompt 工作流保留为参考，当前版本提供可运行的规则引擎。

## 快速运行

需要 Python 3.10+。在仓库根目录运行，基础功能无需安装第三方依赖：

```bash
python -m taskgen generate --input examples/atomic_tasks.json --config config.example.json --output generated/demo
python -m taskgen validate --input generated/demo/tasks.jsonl --config config.example.json
```

示例包含两条虚构原子任务，生成 14 条变体，每条任务恰好 7 条。预览 [平台导出示例](examples/expected/platform_tasks.csv) 和 [校验报告](examples/expected/report.json)。

使用 Excel 文件作为输入或输出时，安装可选依赖：

```bash
python -m pip install '.[excel]'
python -m taskgen generate --input examples/atomic_tasks.csv --config config.example.json --output generated/excel --xlsx
```

安装后也可以使用 `taskgen generate ...` 命令。支持 JSON、CSV、XLSX 原子任务输入；XLSX 读取第一个工作表，要求使用字面值而非公式。

## 解决的问题

原子任务不应该只被改写成若干句不同的话。采集任务需要可控地改变物体属性、起始位置、目标位置等条件，同时满足场景物料和业务规则。

本工具将以下工作分开：

| 环节 | 当前实现 |
| --- | --- |
| 任务定义 | 保存原始指令、场景、动作、基础参数、初始状态和结构化目标 |
| 泛化配置 | 通过 JSON 定义维度、允许取值、场景物料、库存和禁配规则 |
| 变体生成 | 单维遍历或双维交叉；每次从基础参数重新生成 |
| 任务校验 | 检查物料取值、数量限制、字段、布尔类型、约束、重复与维度覆盖 |
| 数量控制 | 每条原子任务达到规定数量才整组导出；不足时记录原因 |
| 批量导出 | JSONL 完整记录、CSV 平台模板、JSON 报告；可选 XLSX |
| 可追溯性 | 保留原子任务 ID、变体 ID、参数、变化维度、指纹和输入/配置哈希 |

这里的“重置”是生成过程中的参数状态隔离：每条候选变体重新复制基础参数，不继承上一条变体的改动。另有 `reset_instruction` 描述现场准备步骤；程序不会实际复位机器人、物体或仿真器。

## 两种生成模式

### 单维遍历：single-axis

每条候选只修改一个维度。按维度轮转生成候选，再优先选择覆盖尚未覆盖维度的候选。

```bash
python -m taskgen generate --input examples/atomic_tasks.json --config config.example.json --mode single-axis --output generated/single
```

示例配置的七个维度是颜色、尺寸、材质、起始位置、目标位置、初始姿态、数量。这是公开演示配置，不是任何客户历史七项规则的复原。

“七个维度”和“七条变体”是两个配置概念。默认配额为 7，且要求覆盖任务启用的全部维度；可以按真实需求修改配额，或在单条任务的 `axes` 中指定适用维度。

### 双维交叉：cross

每条候选同时修改两个维度，例如颜色与位置。先检查场景约束，再按配额选取有效候选。

```bash
python -m taskgen generate --input examples/atomic_tasks.json --config config.example.json --mode cross --output generated/cross
```

这是受限的双维组合生成，不保证覆盖所有参数对，也不进行无限笛卡尔积展开。候选检查由 `max_candidates` 限制，达到上限会写入报告。某个必需维度没有有效候选时，整组拒绝。

## 输入与规则

完整输入见 [JSON 示例](examples/atomic_tasks.json) 和 [CSV 示例](examples/atomic_tasks.csv)。每条任务需要：

- `task_id`、`task_name`、`scene_id`、`action`、`original_instruction`。
- `parameters`：基础物体和状态参数。
- `goal`：结构化目标，例如物体、目标区域和数量。
- `instruction_template`、`initial_state_template`、`goal_template`、`reset_template`：使用简单的 `{参数名}` 占位符。
- 可选 `axes`、`variants_per_task`、`labels`。省略 labels 时使用文档化的演示默认值；实际采集前应核对任务类型、工具使用、灵巧度与用时。

CSV/XLSX 中的 `parameters`、`goal`、`labels`、`axes` 使用 JSON 单元格。输入已经泛化过的旧版 `input_example.csv` 不属于新 CLI 的原子任务输入格式，保留作原始工作流参考。

[配置文件](config.example.json) 包含：

- `axes`：变化维度与候选取值。
- `scenes.*.allowed_values`：该场景允许使用的物体、位置与属性。
- `scenes.*.inventory`：每类物体可用数量。
- `constraints`：全局约束，或场景内独立约束。
- `different` 规则：指定字段取值必须不同。
- `forbid` 规则：禁止特定属性组合。
- `require_axis_coverage`：是否要求覆盖全部启用维度，默认 true。
- `deduplicate_across_tasks`：是否在批次内跨原子任务去重，默认 true。

例如，库存只有两个杯子时，数量为三的候选会被剔除。禁止“金属＋侧放”的演示约束也会过滤对应交叉组合。规则只表达已配置的限制，不代表现实中的通用物理规律。

## 客户表格、人工审核与交付

v0.3 增加普通中文列名的 CSV/XLSX 适配，无需客户在单元格中填写 JSON。通过[字段映射配置](examples/customer_mapping.json)指定列名、场景别名、任务模板与参数类型，参考[客户表格示例](examples/customer_tasks.csv)。映射仍需人工确认：只有一列自然语言指令的表格不能直接推断出全部任务条件。

```bash
python -m taskgen import-sheet --input examples/customer_tasks.csv --mapping examples/customer_mapping.json --config config.example.json --output generated/import
python -m taskgen generate --input generated/import/imported_tasks.json --config config.example.json --output generated/customer
python -m taskgen create-review --input generated/customer/tasks.jsonl --config config.example.json --output generated/review.xlsx
# 人工在审核表中选择“通过”或“退回”，退回时填写修改意见。
python -m taskgen finalize --input generated/customer/tasks.jsonl --review generated/review.xlsx --config config.example.json --output generated/delivery --xlsx
```

- 缺失参数、未知模板等记录进入 `incomplete_rows.csv`；`import_report.json` 保留源行号、文件与映射哈希，合法行可继续处理。
- 审核表默认全部“待审核”，XLSX 提供状态下拉框。每组原子任务的全部变体通过后才进入交付文件；遗漏、待审核或退回会暂缓整组，`finalize` 返回 1。
- 审核表绑定规范记录的批次及内容指纹。直接改动任务描述、混入旧批次或重复变体会被拒绝；修改任务应回到源数据重新生成，再审核。
- 输出 `delivery.csv`、可选 `delivery.xlsx`、`delivery_report.json` 和 `review_audit.json`，保留逐条审核结果。哈希用于一致性核对，不证明审核者身份；当前是本地单文件流程，不包含多人权限或真实平台上传。
- 生成报告新增各维度已观察取值、变体数量与双维组合计数，便于检查生成分布。它不代表全部可行组合的覆盖率或真实采集成功率。

## 生成文件与错误处理

| 文件 | 内容 |
| --- | --- |
| `tasks.jsonl` | 规范记录，保留基础参数、变体参数、目标、来源和指纹；用于二次校验 |
| `platform_tasks.csv` | 任务名称、描述、目标、初始状态、变化维度、任务类型、环境和 Label 等平台列 |
| `report.json` | 接受/拒绝数量、每组覆盖维度、候选拒绝原因、输入与配置哈希 |
| `platform_tasks.xlsx` | 加 `--xlsx` 时生成的 Excel 视图 |

Label 是有效 JSON，工具使用标记保留为真正的布尔值。表格中的危险公式前缀会转义；规范 JSONL 保留原始文本。平台列是示例适配格式，接入真实平台前仍需核对字段契约。

重复依据是“场景＋动作＋参数＋结构化目标”的 SHA-256 指纹；仅修改文字措辞不会生成新的结构化任务。指纹用于内容标识，不是安全签名，也不提供通用的自然语言语义去重。

退出码：

- `0`：全部原子任务完整生成，或记录校验通过。
- `1`：存在拒绝任务或记录。合法原子任务的完整组仍可导出，原因见报告；不足配额的组不会部分导出。
- `2`：配置、文件格式或 IO 错误。

输出目录内的同名生成文件会被替换；其他文件保留。

## 测试与验证

```bash
python -m unittest discover -s tests -v
```

测试覆盖单维状态隔离、双维交叉、物料/库存约束、数量不足、维度覆盖、结构化去重、字段篡改、客户字段映射、缺失参数、批次绑定审核、整组交付、退出码及 CSV/JSONL 往返；安装 Excel extra 后也测试 XLSX 输入、输出、审核下拉框与公式拒绝。

另有“8 个演示场景 × 每场景 21 条合成原子任务 × 7 条变体”的批量正确性测试，预期 1,176 条。它是合成测试夹具，不是客户采集数据、历史产量或性能基准。

## 能力边界与项目演进

- 本版本从已结构化的原子任务生成变体，不自动理解任意自然语言，也不调用 LLM API。可以由人工或上游模型提供结构化输入；进入生成器后仍执行相同的规则校验。
- 物理可执行性、目标语义和最终采集适用性需要人工或仿真复核，所有输出保留 `requires_human_review=true`。
- 当前未集成机器人控制、仿真、运动规划或真实采集平台 API。
- 早期 Prompt、`SKILL.md` 和根目录 CSV 保留为参考。当前 Python 引擎是 2026 年 11 月的后续工程化扩展，不应被描述为早期实习期间已部署的功能。
- 全部示例是公开构造的数据，不含客户名单、内部平台、真实任务库或业务凭据。

## 目录与设计参考

```text
taskgen/                 生成引擎、格式适配与 CLI
config.example.json      可运行的演示规则
examples/atomic_tasks.*  同一组 JSON / CSV 原子任务
examples/customer_*      普通客户表格与显式字段映射
examples/expected/       可复现的演示输出
tests/                   单元与 CLI 测试
docs/design.md           设计决策与验证边界
docs/references.md       参考项目及借鉴范围
SKILL.md                 原始 Prompt 工作流参考
variant-standardizer.txt 原始标准化 Prompt
```

参考了 RLBench 的任务变体与实例区分、LIBERO 的结构化任务生成、BEHAVIOR 的初始状态与目标条件建模；当前实现为独立编写的轻量规则工具，不依赖这些仿真框架。[参考说明](docs/references.md)

## License

MIT. See [LICENSE](LICENSE).

