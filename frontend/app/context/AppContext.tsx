"use client";

import React, { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
import {
  cancelJob,
  createJob,
  downloadExport,
  fetchProviders,
  streamEvents,
  type ExportMode,
  type ProviderInfo,
  type RhEvent,
} from "../../lib/api";
import type { NodeState, NodeStatus } from "../components/shared/NodeCard";

export type Phase = "idle" | "running" | "done" | "error";

export interface ChatHistoryItem {
  jobId: string;
  question: string;
  timestamp: number;
  pipeline: string;
  finalStatus: Phase;
  savedNodes?: Record<string, NodeState>;
  savedOrder?: string[];
}

export const PIPELINES = [
  { value: "pipelines/race_round1.yaml", label: "race_round1 · ⚡ 多模型并发竞速接力 (推荐)", shortLabel: "⚡ 竞速接力（推荐）" },
  { value: "pipelines/round1.yaml", label: "round1 · 首轮架构分析", shortLabel: "📋 标准策划" },
  { value: "pipelines/api_smoke.yaml", label: "api_smoke · 官方API连通路线", shortLabel: "🔌 API 连通测试" },
  { value: "pipelines/continue.yaml", label: "continue · 深入再来一轮", shortLabel: "🔄 深入再来一轮" },
];

const FALLBACK_MODELS: ProviderInfo[] = [
  { id: "gemini_api_1", site: "gemini_api", label: "Gemini 2.5 Flash", model: "gemini-2.5-flash", api: true },
  { id: "qwen_api_1", site: "qwen_api", label: "Qwen Plus", model: "qwen-plus", api: true },
  // 京东云 (OpenAI Compatible)
  { id: "jdcloud_deepseek_v3_2", site: "openai_api", label: "DeepSeek-V3.2", model: "DeepSeek-V3.2", api: true },
  { id: "jdcloud_glm_5", site: "openai_api", label: "GLM-5", model: "GLM-5", api: true },
  { id: "jdcloud_glm_4_7", site: "openai_api", label: "GLM-4.7", model: "GLM-4.7", api: true },
  { id: "jdcloud_minimax_m2_5", site: "openai_api", label: "MiniMax-M2.5", model: "MiniMax-M2.5", api: true },
  { id: "jdcloud_kimi_k2_5", site: "openai_api", label: "Kimi-K2.5", model: "Kimi-K2.5", api: true },
  { id: "jdcloud_kimi_k2_turbo", site: "openai_api", label: "Kimi-K2-Turbo", model: "Kimi-K2-Turbo", api: true },
  { id: "jdcloud_qwen3_coder", site: "openai_api", label: "Qwen3-Coder", model: "Qwen3-Coder", api: true },
  // 阿里云 (Anthropic Compatible)
  { id: "aliyun_qwen3_7_plus", site: "anthropic_api", label: "qwen3.7-plus", model: "qwen3.7-plus", api: true },
  { id: "aliyun_qwen3_6_plus", site: "anthropic_api", label: "qwen3.6-plus", model: "qwen3.6-plus", api: true },
  { id: "aliyun_qwen3_5_plus", site: "anthropic_api", label: "qwen3.5-plus", model: "qwen3.5-plus", api: true },
  { id: "aliyun_qwen3_max_20260123", site: "anthropic_api", label: "qwen3-max-2026-01-23", model: "qwen3-max-2026-01-23", api: true },
  { id: "aliyun_qwen3_coder_next", site: "anthropic_api", label: "qwen3-coder-next", model: "qwen3-coder-next", api: true },
  { id: "aliyun_qwen3_coder_plus", site: "anthropic_api", label: "qwen3-coder-plus", model: "qwen3-coder-plus", api: true },
  { id: "aliyun_glm_5", site: "anthropic_api", label: "glm-5", model: "glm-5", api: true },
  { id: "aliyun_glm_4_7", site: "anthropic_api", label: "glm-4.7", model: "glm-4.7", api: true },
  { id: "aliyun_kimi_k2_5", site: "anthropic_api", label: "kimi-k2.5", model: "kimi-k2.5", api: true },
  { id: "aliyun_minimax_m2_5", site: "anthropic_api", label: "MiniMax-M2.5", model: "MiniMax-M2.5", api: true },
  // Web providers
  { id: "chatgpt_web_1", site: "chatgpt", label: "ChatGPT Web", model: null, api: false },
  { id: "claude_web_1", site: "claude", label: "Claude Web", model: null, api: false },
  { id: "kimi_web_1", site: "kimi", label: "Kimi Web", model: null, api: false },
  { id: "deepseek_web_1", site: "deepseek", label: "DeepSeek Web", model: null, api: false },
  { id: "zai_web_1", site: "zai", label: "Z.AI 智谱 Web", model: null, api: false },
  { id: "qwen_web_1", site: "qwen", label: "Qwen 国际版 Web", model: null, api: false },
  { id: "gemini_web_1", site: "gemini", label: "Gemini Web", model: null, api: false },
];

interface AppSettingsContextType {
  theme: "dark" | "light";
  pipeline: string;
  availableModels: ProviderInfo[];
  selectedModels: string[];
  toggleTheme: () => void;
  toggleModel: (id: string) => void;
  selectOnlyAPI: () => void;
  selectAllModels: () => void;
  setPipeline: (pl: string) => void;
}

interface AppJobContextType {
  question: string;
  setQuestion: (q: string) => void;
  phase: Phase;
  order: string[];
  nodes: Record<string, NodeState>;
  jobId: string | null;
  banner: string | null;
  expandedKeys: Record<string, boolean>;
  concurrentBusy: boolean;
  setConcurrentBusy: (b: boolean) => void;
  run: () => Promise<void>;
  cancel: () => Promise<void>;
  againRound: () => void;
  onExport: (mode: ExportMode) => Promise<void>;
  toggleExpand: (key: string) => void;
  chatHistory: ChatHistoryItem[];
  loadHistoryItem: (item: ChatHistoryItem) => void;
  deleteHistoryItem: (jobId: string) => void;
  retryHistoryItem: (item: ChatHistoryItem) => void;
}

const AppSettingsContext = createContext<AppSettingsContextType | null>(null);
const AppJobContext = createContext<AppJobContextType | null>(null);

export const AppContextProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  // Settings state
  const [theme, setTheme] = useState<"dark" | "light">("dark");
  const [pipeline, setPipeline] = useState<string>(PIPELINES[0].value);
  const [availableModels, setAvailableModels] = useState<ProviderInfo[]>(FALLBACK_MODELS);
  const [selectedModels, setSelectedModels] = useState<string[]>([]);

  // Job state
  const [question, setQuestion] = useState<string>("");
  const [phase, setPhase] = useState<Phase>("idle");
  const [order, setOrder] = useState<string[]>([]);
  const [nodes, setNodes] = useState<Record<string, NodeState>>({});
  const [jobId, setJobId] = useState<string | null>(null);
  const [banner, setBanner] = useState<string | null>(null);
  const [expandedKeys, setExpandedKeys] = useState<Record<string, boolean>>({});
  const [concurrentBusy, setConcurrentBusy] = useState<boolean>(false);
  const [chatHistory, setChatHistory] = useState<ChatHistoryItem[]>([]);

  const abortRef = useRef<AbortController | null>(null);

  // Fetch providers
  useEffect(() => {
    fetchProviders().then((list) => {
      if (list.length > 0) {
        setAvailableModels(list);
        const savedModels = localStorage.getItem("rh_selected_models");
        if (savedModels) {
          try {
            setSelectedModels(JSON.parse(savedModels));
          } catch {
            setSelectedModels(list.filter((m) => m.api).map((m) => m.id));
          }
        } else {
          setSelectedModels(list.filter((m) => m.api).map((m) => m.id));
        }
      } else {
        setAvailableModels(FALLBACK_MODELS);
        setSelectedModels(FALLBACK_MODELS.filter((m) => m.api).map((m) => m.id));
      }
    });
  }, []);

  // Sync theme
  useEffect(() => {
    const saved = localStorage.getItem("rh_theme") as "dark" | "light" | null;
    if (saved) {
      setTheme(saved);
      document.documentElement.setAttribute("data-theme", saved);
    } else {
      document.documentElement.setAttribute("data-theme", "dark");
    }
  }, []);

  // Load chat history
  useEffect(() => {
    const saved = localStorage.getItem("rh_chat_history");
    if (saved) {
      try {
        setChatHistory(JSON.parse(saved));
      } catch {
        // ignore error
      }
    }
  }, []);

  const saveHistoryItem = useCallback((id: string, q: string, pl: string, status: Phase, curNodes?: Record<string, NodeState>, curOrder?: string[]) => {
    setChatHistory((prev) => {
      const filtered = prev.filter((item) => item.jobId !== id);
      const newItem: ChatHistoryItem = {
        jobId: id,
        question: q,
        timestamp: Date.now(),
        pipeline: pl,
        finalStatus: status,
        savedNodes: curNodes,
        savedOrder: curOrder,
      };
      const next = [newItem, ...filtered].slice(0, 50);
      localStorage.setItem("rh_chat_history", JSON.stringify(next));
      return next;
    });
  }, []);

  const toggleTheme = useCallback(() => {
    setTheme((prev) => {
      const next = prev === "dark" ? "light" : "dark";
      localStorage.setItem("rh_theme", next);
      document.documentElement.setAttribute("data-theme", next);
      return next;
    });
  }, []);

  const toggleModel = useCallback((id: string) => {
    setSelectedModels((prev) => {
      const next = prev.includes(id) ? (prev.length > 1 ? prev.filter((m) => m !== id) : prev) : [...prev, id];
      localStorage.setItem("rh_selected_models", JSON.stringify(next));
      return next;
    });
  }, []);

  const selectOnlyAPI = useCallback(() => {
    setAvailableModels((models) => {
      const apiModels = models.filter((m) => m.api).map((m) => m.id);
      setSelectedModels(apiModels);
      localStorage.setItem("rh_selected_models", JSON.stringify(apiModels));
      return models;
    });
  }, []);

  const selectAllModels = useCallback(() => {
    setAvailableModels((models) => {
      const allIds = models.map((m) => m.id);
      setSelectedModels(allIds);
      localStorage.setItem("rh_selected_models", JSON.stringify(allIds));
      return models;
    });
  }, []);

  const toggleExpand = useCallback((key: string) => {
    setExpandedKeys((prev) => {
      const isLast = order.length > 0 && order[order.length - 1] === key;
      const current = prev[key] !== undefined ? prev[key] : isLast;
      return { ...prev, [key]: !current };
    });
  }, [order]);

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
    if (ev.type === "step_transitioning") {
      const nextKey = ev.key;
      if (!nextKey) return;
      setOrder((prev) => (prev.includes(nextKey) ? prev : [...prev, nextKey]));
      setNodes((prev) => ({
        ...prev,
        [nextKey]: { key: nextKey, status: "running" as NodeStatus, content: "🔄 正在切换到下一步骤，多模型竞速即将启动...\n" },
      }));
      return;
    }
    const key = ev.key;
    if (!key) return;
    setOrder((prev) => (prev.includes(key) ? prev : [...prev, key]));
    setNodes((prev) => {
      const next = { ...prev };
      const cur = next[key] ?? { key, status: "running" as NodeStatus };
      if (ev.provider) cur.provider = ev.provider;
      if (ev.label) cur.label = ev.label;
      if (ev.model) cur.model = ev.model;
      if (ev.type === "step_queued") {
        cur.status = "running";
        cur.queuedPosition = ev.position;
        cur.content = `⏳ 当前目标模型账号忙碌中，正在排队等待空闲账号（队列第 ${ev.position ?? 1} 位）...`;
      } else if (ev.type === "step_started") {
        cur.status = "running";
        cur.queuedPosition = undefined;
        let text = cur.content || "";
        if (text.startsWith("🔄 正在切换") || text.startsWith("⏳ 当前目标模型账号忙碌")) {
          cur.content = "";
        }
      } else if (ev.type === "step_chunk") {
        cur.status = "running";
        cur.queuedPosition = undefined;
        let text = cur.content || "";
        if (text.startsWith("⚡ 正在同时拉起") || text.startsWith("🔄 正在切换") || text.startsWith("⏳ 当前目标模型账号忙碌")) {
          text = "";
        }
        cur.content = text + (ev.delta || "");
      } else if (ev.type === "step_succeeded") {
        cur.status = "succeeded";
        cur.queuedPosition = undefined;
        let text = ev.content ?? cur.content ?? "";
        if (text.startsWith("⚡ 正在同时拉起") || text.startsWith("🔄 正在切换") || text.startsWith("⏳ 当前目标模型账号忙碌")) text = "";
        cur.content = text;
      } else if (ev.type === "step_failed") {
        cur.status = "failed";
        cur.error = ev.error;
      } else if (ev.type === "runnerup_chunk" && ev.provider) {
        const r = { ...(cur.runnerups || {}) };
        r[ev.provider] = (r[ev.provider] || "") + (ev.delta || "");
        cur.runnerups = r;
        const rt = { ...(cur.runnerupTyping || {}) };
        rt[ev.provider] = true;
        cur.runnerupTyping = rt;
      } else if (ev.type === "runnerup_succeeded" && ev.provider) {
        const r = { ...(cur.runnerups || {}) };
        r[ev.provider] = ev.content ?? r[ev.provider] ?? "";
        cur.runnerups = r;
        const rt = { ...(cur.runnerupTyping || {}) };
        rt[ev.provider] = false;
        cur.runnerupTyping = rt;
        const rl = { ...(cur.runnerupLabels || {}) };
        if (ev.label) rl[ev.provider] = ev.label;
        cur.runnerupLabels = rl;
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
      saveHistoryItem(id, q, pipeline, "running");
      await streamEvents(id, applyEvent, ac.signal);
      setPhase((p) => {
        const finalP = p === "running" ? "done" : p;
        // Save final nodes and order to history
        setNodes((curNodes) => {
          setOrder((curOrder) => {
            saveHistoryItem(id, q, pipeline, finalP, curNodes, curOrder);
            return curOrder;
          });
          return curNodes;
        });
        return finalP;
      });
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
  }, [question, pipeline, selectedModels, phase, applyEvent, saveHistoryItem]);

  const cancel = useCallback(async () => {
    if (!jobId) return;
    try {
      await cancelJob(jobId);
      abortRef.current?.abort();
      setBanner("任务已被主动终止");
      setPhase("error");
      if (question) saveHistoryItem(jobId, question, pipeline, "error");
    } catch (err) {
      setBanner(err instanceof Error ? err.message : "终止任务失败");
    }
  }, [jobId, question, pipeline, saveHistoryItem]);

  const againRound = useCallback(() => {
    const lastKey = [...order].reverse().find((k) => nodes[k]?.status === "succeeded");
    const seed = lastKey ? nodes[lastKey]?.content ?? "" : "";
    setQuestion(seed);
    setPipeline("pipelines/continue.yaml");
    setPhase("idle");
    setBanner(null);
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

  const loadHistoryItem = useCallback((item: ChatHistoryItem) => {
    setQuestion(item.question);
    setPipeline(item.pipeline);
    setJobId(item.jobId);
    setPhase(item.finalStatus);
    if (item.savedNodes) setNodes(item.savedNodes);
    if (item.savedOrder) setOrder(item.savedOrder);
    setExpandedKeys({});
    setBanner(null);
  }, []);

  const deleteHistoryItem = useCallback((jobId: string) => {
    setChatHistory((prev) => {
      const next = prev.filter((item) => item.jobId !== jobId);
      localStorage.setItem("rh_chat_history", JSON.stringify(next));
      return next;
    });
  }, []);

  const retryHistoryItem = useCallback((item: ChatHistoryItem) => {
    setQuestion(item.question);
    setPipeline(item.pipeline);
    setPhase("idle");
    setOrder([]);
    setNodes({});
    setExpandedKeys({});
    setBanner(null);
    setJobId(null);
  }, []);

  const settingsValue: AppSettingsContextType = {
    theme,
    pipeline,
    availableModels,
    selectedModels,
    toggleTheme,
    toggleModel,
    selectOnlyAPI,
    selectAllModels,
    setPipeline,
  };

  const jobValue: AppJobContextType = {
    question,
    setQuestion,
    phase,
    order,
    nodes,
    jobId,
    banner,
    expandedKeys,
    concurrentBusy,
    setConcurrentBusy,
    run,
    cancel,
    againRound,
    onExport,
    toggleExpand,
    chatHistory,
    loadHistoryItem,
    deleteHistoryItem,
    retryHistoryItem,
  };

  return (
    <AppSettingsContext.Provider value={settingsValue}>
      <AppJobContext.Provider value={jobValue}>{children}</AppJobContext.Provider>
    </AppSettingsContext.Provider>
  );
};

export function useAppSettings() {
  const context = useContext(AppSettingsContext);
  if (!context) throw new Error("useAppSettings must be used within AppContextProvider");
  return context;
}

export function useAppJob() {
  const context = useContext(AppJobContext);
  if (!context) throw new Error("useAppJob must be used within AppContextProvider");
  return context;
}
