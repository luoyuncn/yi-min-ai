# WebSearch 移植文档纲要

本文档用于将当前项目中的 `WebSearch` 能力移植到另一个 agent 项目。它只覆盖互联网搜索工具链，不覆盖 workspace 内的 `read_file`、`glob_search`、`grep_search`。

## 1. 移植目标

将当前项目的 `WebSearch` 设计抽取为一个可独立集成的 agent tool：

- agent 能向模型暴露 `WebSearch` 工具定义。
- 模型可通过 tool call 发起网络搜索。
- 工具执行器负责请求搜索后端、解析结果、过滤域名、去重、截断。
- 工具结果以结构化 JSON 回灌给模型。
- 模型最终回答可基于结果生成带 Sources 的回答。

当前项目参考实现：

- 工具声明与分发：`rust/crates/tools/src/lib.rs`
- agent 工具执行闭环：`rust/crates/runtime/src/conversation.rs`
- CLI 工具执行器：`rust/crates/rusty-claude-cli/src/main.rs`

## 2. 当前实现边界

`WebSearch` 是一个只读工具，职责是“搜索并返回可引用的结果”，不是浏览器，也不是网页正文抓取器。

它做：

- 构造搜索 URL。
- 发起 HTTP GET。
- 解析搜索结果 HTML。
- 抽取 `title` 和 `url`。
- 支持 `allowed_domains` 与 `blocked_domains`。
- 按 URL 去重。
- 最多返回 8 条。
- 附带一段给模型的提示，要求最终回答包含 Sources。

它不做：

- 不执行 JavaScript。
- 不打开浏览器。
- 不抓取每个搜索结果页面正文。
- 不做结果摘要。
- 不做复杂 ranking。

## 3. 工具契约

### 3.1 Tool 名称

```text
WebSearch
```

### 3.2 权限级别

```text
ReadOnly
```

移植时建议将它归入“联网只读工具”。如果目标 agent 有网络权限开关，应在执行前检查网络权限。

### 3.3 输入 Schema

```json
{
  "type": "object",
  "properties": {
    "query": {
      "type": "string",
      "minLength": 2
    },
    "allowed_domains": {
      "type": "array",
      "items": { "type": "string" }
    },
    "blocked_domains": {
      "type": "array",
      "items": { "type": "string" }
    }
  },
  "required": ["query"],
  "additionalProperties": false
}
```

### 3.4 输入类型

```rust
struct WebSearchInput {
    query: String,
    allowed_domains: Option<Vec<String>>,
    blocked_domains: Option<Vec<String>>,
}
```

其他语言可等价表示为：

```ts
type WebSearchInput = {
  query: string;
  allowed_domains?: string[];
  blocked_domains?: string[];
};
```

### 3.5 输出类型

```rust
struct WebSearchOutput {
    query: String,
    results: Vec<WebSearchResultItem>,
    duration_seconds: f64,
}

struct SearchHit {
    title: String,
    url: String,
}
```

当前项目中的 `results` 是 untagged enum，实际 JSON 输出形态类似：

```json
{
  "query": "rust web search",
  "results": [
    "Search results for \"rust web search\". Include a Sources section in the final answer.\n- [Reqwest docs](https://docs.rs/reqwest)",
    {
      "tool_use_id": "web_search_1",
      "content": [
        {
          "title": "Reqwest docs",
          "url": "https://docs.rs/reqwest"
        }
      ]
    }
  ],
  "durationSeconds": 0.42
}
```

移植时可以简化为更规整的结构：

```ts
type WebSearchOutput = {
  query: string;
  results: SearchHit[];
  commentary: string;
  durationSeconds: number;
  provider?: string;
  finalUrl?: string;
};

type SearchHit = {
  title: string;
  url: string;
};
```

## 4. 推荐模块拆分

建议在目标 agent 中拆成以下模块。

```text
web_search/
  tool_definition
  input_validation
  provider
  parser
  filtering
  executor
  tests
```

### 4.1 Tool Definition

职责：

- 返回工具名、描述、输入 schema、权限级别。
- 将 `WebSearch` 暴露给模型。

接口建议：

```ts
function webSearchToolDefinition(): ToolDefinition
```

### 4.2 Provider

职责：

- 根据 query 构造搜索 URL。
- 发 HTTP 请求。
- 返回 HTML 或 provider 原始结果。

当前项目 provider 策略：

- 默认使用 DuckDuckGo HTML：

```text
https://html.duckduckgo.com/html/?q=<query>
```

- 如果设置环境变量，则使用自定义 base URL：

```text
CLAWD_WEB_SEARCH_BASE_URL=<base-url>
```

自定义 URL 会追加 `q=<query>` 参数。

接口建议：

```ts
interface SearchProvider {
  search(query: string): Promise<SearchProviderResponse>;
}

type SearchProviderResponse = {
  html: string;
  finalUrl: string;
  statusCode?: number;
  provider: string;
};
```

### 4.3 Parser

职责：

- 从 HTML 中抽取搜索结果。
- 优先支持 DuckDuckGo HTML。
- 结果为空时 fallback 到通用 `<a href="">title</a>` 解析。

接口建议：

```ts
interface SearchResultParser {
  parse(html: string): SearchHit[];
}
```

当前项目解析策略：

1. 查找包含 `result__a` 的 anchor。
2. 从 anchor 附近提取 `href`。
3. 提取 anchor 内文本作为 title。
4. 解码 DuckDuckGo redirect URL。
5. 清理 HTML tag 和 HTML entity。
6. 如果没有命中，则解析普通链接。

### 4.4 Filtering

职责：

- `allowed_domains` 白名单过滤。
- `blocked_domains` 黑名单过滤。
- URL 去重。
- 限制最大条数。

当前规则：

- domain filter 会先归一化：

```text
https://DOCS.rs/ -> docs.rs
.docs.rs        -> docs.rs
docs.rs/        -> docs.rs
```

- host 匹配：

```text
host == domain
or host.ends_with("." + domain)
```

- 处理顺序：

```text
allowed_domains -> blocked_domains -> dedupe -> truncate
```

### 4.5 Executor

职责：

- 校验输入。
- 记录开始时间。
- 调用 provider。
- 调用 parser。
- 调用 filtering。
- 组装输出 JSON。

接口建议：

```ts
async function executeWebSearch(input: WebSearchInput): Promise<WebSearchOutput>
```

## 5. 核心执行流程

```text
executeWebSearch(input)
  |
  |-- validate input
  |     query length >= 2
  |
  |-- start timer
  |
  |-- provider.search(query)
  |     default: DuckDuckGo HTML
  |     optional: env/custom provider
  |
  |-- parseDuckDuckGoHtml(html)
  |
  |-- if no hits:
  |     parseGenericLinks(html)
  |
  |-- apply allowed_domains
  |
  |-- apply blocked_domains
  |
  |-- dedupe by url
  |
  |-- limit to max results
  |
  |-- build commentary
  |
  |-- return structured output
```

## 6. HTTP 客户端要求

当前项目使用：

```text
timeout: 20s
redirects: max 10
user-agent: clawd-rust-tools/0.1
TLS: rustls
```

移植建议：

- 设置明确超时，避免 agent 卡死。
- 限制重定向次数。
- 设置稳定 User-Agent。
- 对网络错误、超时、无效 URL 做结构化错误。
- 可配置代理或企业网络参数。

## 7. DuckDuckGo URL 解码

DuckDuckGo HTML 结果常见链接：

```text
https://duckduckgo.com/l/?uddg=https%3A%2F%2Fdocs.rs%2Freqwest
//duckduckgo.com/l/?uddg=https%3A%2F%2Fdocs.rs%2Ftokio
```

解析规则：

1. HTML entity decode。
2. 如果是 `//duckduckgo.com/...`，补成 `https://...`。
3. 如果 host 是 `duckduckgo.com` 或 `*.duckduckgo.com`，且 path 是 `/l` 或 `/l/`：
   - 读取 query 参数 `uddg`。
   - URL decode 后作为真实目标 URL。
4. 如果本来就是 `http://` 或 `https://`，直接返回。

## 8. Agent 集成方式

目标 agent 中应接入到标准 tool loop：

```text
model request includes WebSearch schema
  |
model emits tool_use:
  name = "WebSearch"
  input = {"query": "..."}
  |
agent validates and authorizes tool
  |
agent executes executeWebSearch
  |
agent appends tool_result to conversation
  |
model continues and writes final answer with sources
```

注意点：

- 工具执行结果要保留完整结构化 JSON。
- UI/终端展示可以截断，但 session 内不要截断。
- tool result 中最好包含 `commentary` 或等价提示，提醒模型最终回答带 Sources。
- 如果目标 agent 支持并行 tool calls，`WebSearch` 可并行执行，但要注意网络限流。

## 9. 错误处理建议

当前项目主要用 `Result<T, String>` 返回错误。移植时建议结构化：

```ts
type WebSearchError =
  | { type: "invalid_input"; message: string }
  | { type: "invalid_provider_url"; message: string }
  | { type: "network_error"; message: string }
  | { type: "timeout"; message: string }
  | { type: "http_error"; statusCode: number; message: string }
  | { type: "parse_empty"; message: string }
  | { type: "filtered_empty"; message: string };
```

推荐行为：

- 网络错误：返回 tool error，让模型知道搜索失败。
- 解析为空：可返回空结果和 commentary，而不是硬错误。
- 过滤后为空：返回空结果，并说明没有结果匹配 domain 规则。
- provider URL 配置错误：硬错误，便于开发者修配置。

## 10. 测试清单

从当前项目可提炼这些测试。

### 10.1 基础解析

- 给定 DuckDuckGo 风格 HTML：
  - `<a class="result__a" href="https://docs.rs/reqwest">Reqwest docs</a>`
  - 应返回一条 `{ title, url }`。

### 10.2 Domain 过滤

- `allowed_domains = ["https://DOCS.rs/"]`
- `blocked_domains = ["HTTPS://EXAMPLE.COM"]`
- 结果中只保留 `docs.rs`。

### 10.3 通用链接 fallback

- HTML 中没有 `result__a`。
- 包含普通 `<a href="https://example.com/one">Example One</a>`。
- 应 fallback 并解析普通链接。

### 10.4 去重

- 两个结果 URL 相同。
- 应只保留第一条。

### 10.5 DuckDuckGo redirect 解码

- absolute redirect：

```text
https://duckduckgo.com/l/?uddg=https%3A%2F%2Fdocs.rs%2Freqwest
```

- protocol-relative redirect：

```text
//duckduckgo.com/l/?uddg=https%3A%2F%2Fdocs.rs%2Ftokio
```

- 都应解码为真实目标 URL。

### 10.6 Provider 配置

- 设置 `CLAWD_WEB_SEARCH_BASE_URL=http://127.0.0.1:<port>/search`
- 请求应为：

```text
GET /search?q=<query>
```

### 10.7 无效 provider URL

- 设置 `CLAWD_WEB_SEARCH_BASE_URL=://bad-base-url`
- 应返回配置错误。

### 10.8 截断

- 返回超过最大条数。
- 应按顺序截断到 `maxResults`，当前项目固定为 8。

## 11. 推荐改进项

当前实现足够轻量，但移植到正式 agent 时建议增强：

- 使用 HTML parser 替代字符串扫描。
- 增加 `max_results` 输入参数。
- 增加 `region`、`language`、`recency`、`site` 参数。
- 支持多个 provider：
  - DuckDuckGo HTML
  - Brave Search API
  - SerpAPI
  - Bing Web Search API
  - 自建搜索代理
- 增加 provider 返回状态：

```json
{
  "provider": "duckduckgo-html",
  "finalUrl": "...",
  "statusCode": 200
}
```

- 区分 `parse_empty` 与 `filtered_empty`。
- 加缓存，避免同一 turn 内重复搜索同一 query。
- 加速率限制，避免模型循环搜索。
- 增加搜索结果可信度字段，如 `sourceHost`。

## 12. 最小移植清单

如果只要最快可用版本，移植以下能力即可：

- `WebSearchInput`
- `SearchHit`
- `WebSearchOutput`
- `executeWebSearch`
- `buildSearchUrl`
- `buildHttpClient`
- `parseDuckDuckGoHtml`
- `parseGenericLinks`
- `decodeDuckDuckGoRedirect`
- `normalizeDomainFilter`
- `hostMatchesList`
- `dedupeHits`
- agent tool loop 中的 `WebSearch` 分发

最小版本依赖：

```text
HTTP client
URL parser
HTML entity decode
basic HTML/link parser
JSON serializer
agent tool dispatcher
```

## 13. 目标项目开发顺序

建议按这个顺序开发：

1. 定义 `WebSearch` tool schema。
2. 实现 `WebSearchInput` 校验。
3. 实现 provider，先支持 DuckDuckGo HTML 或 mock base URL。
4. 实现 parser，先覆盖 `result__a`。
5. 实现 DuckDuckGo redirect 解码。
6. 实现 generic link fallback。
7. 实现 allow/block domain。
8. 实现 dedupe 和 limit。
9. 接入 agent tool executor。
10. 接入模型 tool loop。
11. 加测试服务器，复刻当前项目测试。
12. 再增加正式 search provider 或高级参数。

## 14. 参考伪代码

```ts
async function executeWebSearch(input: WebSearchInput): Promise<WebSearchOutput> {
  validateWebSearchInput(input);

  const started = Date.now();
  const response = await provider.search(input.query);

  let hits = parseDuckDuckGoHtml(response.html);
  if (hits.length === 0) {
    hits = parseGenericLinks(response.html);
  }

  if (input.allowed_domains?.length) {
    hits = hits.filter(hit => hostMatchesList(hit.url, input.allowed_domains!));
  }

  if (input.blocked_domains?.length) {
    hits = hits.filter(hit => !hostMatchesList(hit.url, input.blocked_domains!));
  }

  hits = dedupeHits(hits).slice(0, 8);

  const commentary =
    hits.length === 0
      ? `No web search results matched the query ${JSON.stringify(input.query)}.`
      : buildSourcesCommentary(input.query, hits);

  return {
    query: input.query,
    commentary,
    results: hits,
    durationSeconds: (Date.now() - started) / 1000,
    provider: response.provider,
    finalUrl: response.finalUrl
  };
}
```

## 15. 验收标准

移植完成后，应满足：

- 模型能看到 `WebSearch` tool definition。
- 模型能发起 `WebSearch` tool call。
- agent 能执行搜索并返回 JSON tool result。
- 支持 domain allow/block。
- 支持 DuckDuckGo redirect 解码。
- 支持 mock provider 测试。
- 搜索失败不会导致整个 agent 崩溃。
- 最终回答能引用 `title/url` 来源。
