"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  cancelJob,
  createJob,
  downloadExport,
  streamEvents,
  USE_MOCK,
  type ExportMode,
  type RhEvent,
} from "../lib/api";
import { renderMarkdown } from "../lib/markdown";
import ModelProbe from "./components/ModelProbe";
import AvatarScene from "./components/AvatarScene";
import AgentLogo from "./components/AgentLogo";

type Phase = "idle" | "running" | "done" | "error";
type NodeStatus = "running" | "succeeded" | "failed";

interface NodeState {
  key: string;
  provider?: string;
  status: NodeStatus;
  content?: string;
  error?: { type: string; message: string };
}

const PIPELINES = [
  { value: "pipelines/race_round1.yaml", label: "race_round1 · ⚡ 多模型并发竞速接力 (推荐)" },
  { value: "pipelines/round1.yaml", label: "round1 · 首轮架构分析" },
  { value: "pipelines/api_smoke.yaml", label: "api_smoke · 官方API连通路线" },
  { value: "pipelines/continue.yaml", label: "continue · 深入再来一轮" },
];

const AVAILABLE_MODELS = [
  { id: "openai_api_1", label: "OpenAI 直连/中转 API", provider: "openai_api", api: true },
  { id: "gemini_api_1", label: "Gemini 官方 API", provider: "gemini_api", api: true },
  { id: "anthropic_api_1", label: "Anthropic 直连 API", provider: "anthropic_api", api: true },
  { id: "qwen_api_1", label: "通义千问 Qwen API", provider: "qwen_api", api: true },
  { id: "chatgpt_web_1", label: "ChatGPT Web 网页", provider: "chatgpt", api: false },
  { id: "claude_web_1", label: "Claude Web 网页", provider: "claude", api: false },
  { id: "kimi_web_1", label: "Kimi Web 网页", provider: "kimi", api: false },
  { id: "deepseek_web_1", label: "DeepSeek Web 网页", provider: "deepseek", api: false },
  { id: "zai_web_1", label: "智谱清言 Z.AI Web 网页", provider: "zai", api: false },
  { id: "qwen_web_1", label: "Qwen 国际版 Web (chat.qwen.ai)", provider: "qwen", api: false },
  { id: "gemini_web_1", label: "Gemini Web 网页 (gemini.google.com)", provider: "gemini", api: false },
];

const BADGE: Record<NodeStatus, string> = {
  running: "运行中",
  succeeded: "完成",
  failed: "失败",
};

const STEP_LABELS: Record<string, string> = {
  generate: "1/5 初稿生成",
  review: "2/5 交叉评审",
  deep_analyze: "3/5 逻辑拆解",
  improve: "4/5 方案优化",
  summary: "5/5 总结收尾",
};

export default function Page() {
  const [theme, setTheme] = useState<"dark" | "light">("dark");
  const [question, setQuestion] = useState("");
  const [pipeline, setPipeline] = useState(PIPELINES[0].value);
  const [phase, setPhase] = useState<Phase>("idle");
  const [order, setOrder] = useState<string[]>([]);
  const [nodes, setNodes] = useState<Record<string, NodeState>>({});
  const [jobId, setJobId] = useState<string | null>(null);
  const [banner, setBanner] = useState<string | null>(null);
  const [expandedKeys, setExpandedKeys] = useState<Record<string, boolean>>({});
  const [concurrentBusy, setConcurrentBusy] = useState(false);
  const [selectedModels, setSelectedModels] = useState<string[]>([
    "openai_api_1",
    "gemini_api_1",
    "anthropic_api_1",
    "chatgpt_web_1",
    "kimi_web_1",
  ]);
  const abortRef = useRef<AbortController | null>(null);

  const toggleModel = (id: string) => {
    setSelectedModels((prev) =>
      prev.includes(id) ? (prev.length > 1 ? prev.filter((m) => m !== id) : prev) : [...prev, id]
    );
  };

  const selectOnlyAPI = () => {
    setSelectedModels(["openai_api_1", "gemini_api_1", "anthropic_api_1", "qwen_api_1"]);
  };

  const selectAllModels = () => {
    setSelectedModels(AVAILABLE_MODELS.map((m) => m.id));
  };

  const toggleExpand = useCallback((key: string) => {
    setExpandedKeys((prev) => {
      const isLast = order.length > 0 && order[order.length - 1] === key;
      const current = prev[key] !== undefined ? prev[key] : isLast;
      return { ...prev, [key]: !current };
    });
  }, [order]);

  // Sync theme with html data attribute & localStorage
  useEffect(() => {
    const saved = localStorage.getItem("rh_theme") as "dark" | "light" | null;
    if (saved) {
      setTheme(saved);
      document.documentElement.setAttribute("data-theme", saved);
    } else {
      document.documentElement.setAttribute("data-theme", "dark");
    }
  }, []);

  const toggleTheme = () => {
    const next = theme === "dark" ? "light" : "dark";
    setTheme(next);
    localStorage.setItem("rh_theme", next);
    document.documentElement.setAttribute("data-theme", next);
  };

  const applyEvent = useCallback((ev: RhEvent) => {
    if (ev.type === "pipeline_finished") {
      setPhase("done");
      return;
    }
    if (ev.type === "fatal") {
      setBanner(ev.error ? `${ev.error.type}: ${ev.error.message}` : "运行中断");
      setPhase("error");
      return;
    }
    const key = ev.key;
    if (!key) return;
    setOrder((prev) => (prev.includes(key) ? prev : [...prev, key]));
    setNodes((prev) => {
      const next = { ...prev };
      const cur = next[key] ?? { key, status: "running" as NodeStatus };
      if (ev.provider) cur.provider = ev.provider;
      if (ev.type === "step_started") cur.status = "running";
      else if (ev.type === "step_chunk") {
        cur.status = "running";
        cur.content = (cur.content || "") + (ev.delta || "");
      } else if (ev.type === "step_succeeded") {
        cur.status = "succeeded";
        cur.content = ev.content ?? cur.content ?? "";
      } else if (ev.type === "step_failed") {
        cur.status = "failed";
        cur.error = ev.error;
      }
      next[key] = { ...cur };
      return next;
    });
  }, []);

  const run = useCallback(async () => {
    const q = question.trim();
    if (!q || phase === "running") return;
    abortRef.current?.abort();
    const ac = new AbortController();
    abortRef.current = ac;

    setBanner(null);
    setOrder([]);
    setNodes({});
    setExpandedKeys({});
    setConcurrentBusy(false);
    setJobId(null);
    setPhase("running");

    try {
      const id = await createJob(q, pipeline, selectedModels);
      setJobId(id);
      await streamEvents(id, applyEvent, ac.signal);
      setPhase((p) => (p === "running" ? "done" : p));
    } catch (err) {
      if (ac.signal.aborted) return;
      const msg = err instanceof Error ? err.message : "提交失败";
      if (msg.includes("429") || msg.includes("max concurrent") || msg.includes("rate limit")) {
        setConcurrentBusy(true);
        setPhase("idle");
      } else {
        setBanner(msg);
        setPhase("error");
      }
    }
  }, [question, pipeline, phase, applyEvent]);

  const againRound = useCallback(() => {
    const lastKey = [...order].reverse().find((k) => nodes[k]?.status === "succeeded");
    const seed = lastKey ? nodes[lastKey]?.content ?? "" : "";
    setQuestion(seed);
    setPipeline("pipelines/continue.yaml");
    setPhase("idle");
    setBanner(null);
    window.scrollTo({ top: 0, behavior: "smooth" });
  }, [order, nodes]);

  const onExport = useCallback(
    async (mode: ExportMode) => {
      if (!jobId) return;
      try {
        await downloadExport(jobId, mode);
      } catch (err) {
        setBanner(err instanceof Error ? err.message : "导出失败");
      }
    },
    [jobId]
  );

  const onCancel = useCallback(async () => {
    if (!jobId) return;
    try {
      await cancelJob(jobId);
      abortRef.current?.abort();
      setBanner("任务已被主动终止");
      setPhase("error");
    } catch (err) {
      setBanner(err instanceof Error ? err.message : "终止任务失败");
    }
  }, [jobId]);

  const running = phase === "running";
  const finished = phase === "done";
  const statusDot = running ? "run" : phase === "done" ? "ok" : phase === "error" ? "err" : "";
  const statusText =
    phase === "idle"
      ? "系统待命就绪"
      : running
      ? jobId
        ? `智能接力协作中 · 任务 ID ${jobId.slice(0, 8)}`
        : "提交请求中…"
      : phase === "done"
      ? "所有协作流程已完成"
      : "运行中断或出错";

  // 获取当前正在运行节点的 Provider 和 Step 名字
  const currentRunningKey = order.find((k) => nodes[k]?.status === "running");
  const currentProvider = currentRunningKey ? nodes[currentRunningKey]?.provider : undefined;

  return (
    <main className="wrap">
      {/* 顶部 Navigation / Masthead */}
      <header className="masthead">
        <div className="brand-group">
          <div className="brand-row">
            <span className="brand">RHCLOUD V1</span>
            <h1>AI 智能接力协作控制台</h1>
          </div>
          <span className="sub">multi-model relay console {USE_MOCK ? "· MOCK 模式" : ""}</span>
        </div>

        <div className="masthead-actions">
          {/* 大模型可用性探针（右侧图标展示） */}
          <ModelProbe />

          {/* 深色 / 浅色皮肤切换按钮 */}
          <button className="theme-toggle" onClick={toggleTheme} title="切换外观主题">
            {theme === "dark" ? "☀️ 浅色" : "🌙 深色"}
          </button>
        </div>
      </header>

      {/* 交互控制面板 */}
      <section className="panel">
        <label className="field" htmlFor="q">
          输入您的任务或提问 (Prompt)
        </label>
        <textarea
          id="q"
          rows={4}
          placeholder="例如：帮我设计一个高并发短链服务架构，包含缓存与数据库设计"
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          disabled={running}
        />
        <div className="model-select-wrapper">
          <div className="model-select-header">
            <label className="field" style={{ margin: 0 }}>
              🤖 选择参与接力互动的大模型 (可多选，直连 API 优先)
            </label>
            <div style={{ display: "flex", gap: "8px" }}>
              <button
                type="button"
                className="ghost"
                style={{ padding: "5px 12px", fontSize: "12px" }}
                onClick={selectOnlyAPI}
                disabled={running}
              >
                ⚡ 仅选直连 API
              </button>
              <button
                type="button"
                className="ghost"
                style={{ padding: "5px 12px", fontSize: "12px" }}
                onClick={selectAllModels}
                disabled={running}
              >
                🌐 全选所有模型
              </button>
            </div>
          </div>
          <div className="model-select-grid">
            {AVAILABLE_MODELS.map((m) => {
              const checked = selectedModels.includes(m.id);
              return (
                <label
                  key={m.id}
                  className={`model-pill ${checked ? (m.api ? "active active-api" : "active") : ""}`}
                >
                  <input
                    type="checkbox"
                    checked={checked}
                    onChange={() => toggleModel(m.id)}
                    disabled={running}
                  />
                  <AgentLogo provider={m.provider} size={16} />
                  <span>{m.label}</span>
                  <span className={`model-badge-tag ${m.api ? "api" : "web"}`}>{m.api ? "API" : "Web"}</span>
                </label>
              );
            })}
          </div>
        </div>

        <div className="controls">
          <div className="grow">
            <label className="field" htmlFor="pl">
              选择执行流水线
            </label>
            <select id="pl" value={pipeline} onChange={(e) => setPipeline(e.target.value)} disabled={running}>
              {PIPELINES.map((p) => (
                <option key={p.value} value={p.value}>
                  {p.label}
                </option>
              ))}
            </select>
          </div>
          <button id="start-btn" onClick={run} disabled={running || !question.trim()}>
            {running ? "🚀 智能协同中…" : "🚀 开始接力执行"}
          </button>
          {running && (
            <button className="ghost" onClick={onCancel} style={{ color: "var(--danger)", borderColor: "var(--danger)" }}>
              🛑 终止任务
            </button>
          )}
        </div>
      </section>

      {/* 并发已满或限制时的友好降级交互卡片 */}
      {concurrentBusy && (
        <section className="panel" style={{ border: "1px solid var(--warn)", background: "rgba(251, 191, 36, 0.08)", marginTop: "24px" }}>
          <div style={{ display: "flex", alignItems: "center", gap: "10px", color: "var(--warn)", fontWeight: 700, fontSize: "16px" }}>
            <span style={{ fontSize: "22px" }}>⚠️</span>
            <span>当前通道并发已满 (Max Concurrent Jobs Reached)</span>
          </div>
          <p style={{ margin: "12px 0 18px 0", fontSize: "14px", lineHeight: "1.6", color: "var(--text)" }}>
            系统检测到当前模型通道繁忙。您无需终止任务，建议立即一键切换为高效【直连 API 模型组】继续工作，或点击重试：
          </p>
          <div style={{ display: "flex", gap: "12px", flexWrap: "wrap" }}>
            <button
              type="button"
              onClick={() => {
                setConcurrentBusy(false);
                selectOnlyAPI();
                setTimeout(() => {
                  const btn = document.getElementById("start-btn");
                  if (btn) btn.click();
                }, 100);
              }}
              style={{ background: "linear-gradient(135deg, var(--warn), #f59e0b)", color: "#000" }}
            >
              ⚡ 切换为【直连 API】继续重试
            </button>
            <button
              type="button"
              className="ghost"
              onClick={() => {
                setConcurrentBusy(false);
                const btn = document.getElementById("start-btn");
                if (btn) btn.click();
              }}
            >
              🔄 原模型组重试
            </button>
          </div>
        </section>
      )}

      {/* 动态小人沟通互动办公室场景 (运行中或已有执行步骤时展示) */}
      {(running || phase !== "idle") && (
        <AvatarScene activeProvider={currentProvider} stepKey={currentRunningKey} question={question} />
      )}

      {/* 任务执行时间轴与节点卡片 */}
      {phase !== "idle" && (
        <>
          <div className="progress-banner-card">
            <div className="progress-banner-header">
              <div className="progress-banner-title">
                <span className={`dot ${statusDot}`} />
                <strong style={{ fontSize: "15px" }}>
                  {running
                    ? `⏳ AI 接力协同中 · 当前第 ${Math.min(order.length, 5)} / 5 步`
                    : finished
                    ? "🎉 所有 5 轮多模型协同接力已完美完成"
                    : "⚠️ 任务中断或出错"}
                </strong>
              </div>
              {running && (
                <span className="progress-banner-step-tag">
                  {currentRunningKey ? `👉 正在进行: ${STEP_LABELS[currentRunningKey] || currentRunningKey}` : "🔄 阶段交接与竞速锁定中..."}
                </span>
              )}
            </div>

            <div className="progress-track">
              <div
                className="progress-fill"
                style={{
                  width: `${finished ? 100 : Math.min(100, Math.max(12, (order.length / 5) * 100))}%`,
                }}
              />
            </div>

            {running && (
              <div className="progress-tips-row">
                <span>💡 协同交互提示：多模型接力共分 5 步（生成→评审→拆解→优化→总结），当上一轮模型打字完成后，将自动开启下一轮大模型接力。</span>
              </div>
            )}
          </div>

          <div className="relay">
            {order.map((key, idx) => {
              const n = nodes[key];
              const isLast = idx === order.length - 1;
              const open = expandedKeys[key] !== undefined ? expandedKeys[key] : isLast;
              return (
                <div key={key} className={`node ${n.status}`}>
                  <div className={`card ${n.status} ${open ? "open" : ""}`}>
                    <div
                      className="head"
                      onClick={() => toggleExpand(key)}
                      style={{ cursor: "pointer", userSelect: "none" }}
                      title="点击展开/折叠该模型回答"
                    >
                      <span className="key">{key}</span>
                      {n.provider && (
                        <div className="provider-tag">
                          <AgentLogo provider={n.provider} size={16} />
                          <span>{n.provider}</span>
                        </div>
                      )}
                      <span className="badge">{BADGE[n.status]}</span>
                      <span style={{ fontSize: "12px", color: "var(--muted)", marginLeft: "8px", fontFamily: "var(--mono)" }}>
                        {open ? "▲ 折叠" : "▼ 展开答案"}
                      </span>
                    </div>
                    {open && (n.status === "succeeded" || (n.status === "running" && n.content)) && (
                      <div className="body">
                        <div className="md" dangerouslySetInnerHTML={{ __html: renderMarkdown(n.content || "") }} />
                      </div>
                    )}
                    {open && n.status === "failed" && (
                      <div className="errbox">
                        {n.error ? `${n.error.type}: ${n.error.message}` : "步骤失败"}
                      </div>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        </>
      )}

      {banner && <div className="banner" style={{ marginTop: "20px", color: "var(--danger)" }}>{banner}</div>}

      {/* 导出结果面板 */}
      {finished && jobId && (
        <section className="panel" style={{ marginTop: "28px" }}>
          <h3 style={{ margin: "0 0 16px 0", color: "var(--accent)" }}>📥 任务导出与续写</h3>
          <div className="controls" style={{ marginTop: 0 }}>
            <button className="ghost" onClick={() => onExport("merged")} disabled={USE_MOCK}>
              导出合并 Markdown
            </button>
            <button className="ghost" onClick={() => onExport("steps")} disabled={USE_MOCK}>
              导出单步打包
            </button>
            <button className="ghost" onClick={() => onExport("json")} disabled={USE_MOCK}>
              导出 JSON
            </button>
            <button onClick={againRound}>再来一轮接力</button>
          </div>
          {USE_MOCK && <p style={{ fontSize: "12px", color: "var(--muted)", marginTop: "12px" }}>Mock 模式下没有落盘文件，导出按钮已禁用。连接真实后端后可用。</p>}
        </section>
      )}
    </main>
  );
}
