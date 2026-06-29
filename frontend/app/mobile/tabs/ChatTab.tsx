"use client";

import React, { useRef, useState, useCallback, useEffect } from "react";
import { useAppJob, useAppSettings } from "../../context/AppContext";
import type { ChatHistoryItem } from "../../context/AppContext";
import AgentLogo from "../../components/AgentLogo";
import { triggerDiskCleanup } from "../../../lib/api";

interface ChatTabProps {
  onGoToTask: () => void;
  onGoToSettings: () => void;
}

/* ── Swipeable history row ─────────────────────────────────── */

interface SwipeableHistoryItemProps {
  item: ChatHistoryItem;
  onDelete: (jobId: string) => void;
  onRetry: (item: ChatHistoryItem) => void;
  onLoad: (item: ChatHistoryItem) => void;
  onGoToTask: () => void;
}

const SWIPE_THRESHOLD = 80;

const SwipeableHistoryItem: React.FC<SwipeableHistoryItemProps> = React.memo(
  ({ item, onDelete, onRetry, onLoad, onGoToTask }) => {
    const [offsetX, setOffsetX] = useState(0);
    const startXRef = useRef(0);
    const currentXRef = useRef(0);
    const swipingRef = useRef(false);

    const handleTouchStart = useCallback((e: React.TouchEvent) => {
      startXRef.current = e.touches[0].clientX;
      currentXRef.current = e.touches[0].clientX;
      swipingRef.current = false;
    }, []);

    const handleTouchMove = useCallback((e: React.TouchEvent) => {
      currentXRef.current = e.touches[0].clientX;
      const diff = currentXRef.current - startXRef.current;
      // Only allow left-swipe (negative diff)
      if (diff < -10) {
        swipingRef.current = true;
        setOffsetX(Math.max(diff, -SWIPE_THRESHOLD - 20));
      } else if (!swipingRef.current) {
        setOffsetX(0);
      }
    }, []);

    const handleTouchEnd = useCallback(() => {
      const diff = currentXRef.current - startXRef.current;
      if (diff < -SWIPE_THRESHOLD) {
        setOffsetX(-SWIPE_THRESHOLD);
      } else {
        setOffsetX(0);
      }
    }, []);

    const handleClick = useCallback(() => {
      // If swiped open, close it instead of navigating
      if (offsetX < -10) {
        setOffsetX(0);
        return;
      }

      switch (item.finalStatus) {
        case "error":
          onRetry(item);
          break;
        case "done":
          onLoad(item);
          onGoToTask();
          break;
        case "running":
          onGoToTask();
          break;
        default:
          onLoad(item);
          onGoToTask();
          break;
      }
    }, [item, offsetX, onRetry, onLoad, onGoToTask]);

    const handleDelete = useCallback(
      (e: React.MouseEvent) => {
        e.stopPropagation();
        onDelete(item.jobId);
      },
      [item.jobId, onDelete]
    );

    const statusIcon =
      item.finalStatus === "error"
        ? "🔄"
        : item.finalStatus === "done"
        ? "👁️"
        : item.finalStatus === "running"
        ? "⏳"
        : "📄";

    return (
      <div className="swipeable-history-wrapper">
        {/* Delete button revealed behind the row */}
        <div className="swipeable-delete-zone" onClick={handleDelete}>
          🗑️ 删除
        </div>

        {/* Sliding foreground row */}
        <div
          className="mobile-history-item swipeable-foreground"
          style={{
            transform: `translateX(${offsetX}px)`,
            transition: swipingRef.current ? "none" : "transform 0.25s ease",
          }}
          onTouchStart={handleTouchStart}
          onTouchMove={handleTouchMove}
          onTouchEnd={handleTouchEnd}
          onClick={handleClick}
        >
          <div className="mobile-history-q">
            <span className="history-status-icon">{statusIcon}</span>
            {item.question}
          </div>
          <div className="mobile-history-meta">
            <span>
              {new Date(item.timestamp).toLocaleTimeString([], {
                hour: "2-digit",
                minute: "2-digit",
              })}
            </span>
            <span className={`status-tag ${item.finalStatus}`}>{item.finalStatus}</span>
          </div>
        </div>
      </div>
    );
  }
);

SwipeableHistoryItem.displayName = "SwipeableHistoryItem";

/* ── Main ChatTab ──────────────────────────────────────────── */

export const ChatTab: React.FC<ChatTabProps> = React.memo(({ onGoToTask, onGoToSettings }) => {
  const {
    question,
    setQuestion,
    phase,
    run,
    cancel,
    jobId,
    chatHistory,
    loadHistoryItem,
    deleteHistoryItem,
    retryHistoryItem,
    concurrentBusy,
    setConcurrentBusy,
  } = useAppJob();
  const { availableModels, selectedModels, selectOnlyAPI } = useAppSettings();

  const running = phase === "running";
  const activeModels = availableModels.filter((m) => selectedModels.includes(m.id));

  /* ── 插队倒计时 ─────────────────────────── */
  const [skipQueued, setSkipQueued] = useState(false);   // 已点击插队
  const [countdown, setCountdown] = useState(0);          // 剩余秒数
  const countdownRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // 清理倒计时
  const clearCountdown = useCallback(() => {
    if (countdownRef.current) {
      clearInterval(countdownRef.current);
      countdownRef.current = null;
    }
  }, []);

  // 当 concurrentBusy 变回 false（任务重试后）时，重置状态
  useEffect(() => {
    if (!concurrentBusy) {
      clearCountdown();
      setSkipQueued(false);
      setCountdown(0);
    }
  }, [concurrentBusy, clearCountdown]);

  const handleSkipQueue = useCallback(async () => {
    setSkipQueued(true);
    const WAIT = 30; // 倒计时秒数
    setCountdown(WAIT);

    // 触发后端磁盘清理（即"插队"核心操作）
    try {
      await triggerDiskCleanup();
    } catch {
      // 清理失败时也继续倒计时，不阻断用户流程
    }

    // 倒计时结束后切换为 API 模型并自动重试
    countdownRef.current = setInterval(() => {
      setCountdown((prev) => {
        if (prev <= 1) {
          clearInterval(countdownRef.current!);
          countdownRef.current = null;
          // 切换到纯 API 模型并重试
          selectOnlyAPI();
          setConcurrentBusy(false);
          setTimeout(() => {
            run();
            onGoToTask();
          }, 100);
          return 0;
        }
        return prev - 1;
      });
    }, 1000);
  }, [selectOnlyAPI, setConcurrentBusy, run, onGoToTask, clearCountdown]);

  const handleDelete = useCallback(
    (jobId: string) => {
      deleteHistoryItem(jobId);
    },
    [deleteHistoryItem]
  );

  const handleRetry = useCallback(
    (item: ChatHistoryItem) => {
      retryHistoryItem(item);
    },
    [retryHistoryItem]
  );

  const handleLoad = useCallback(
    (item: ChatHistoryItem) => {
      loadHistoryItem(item);
    },
    [loadHistoryItem]
  );

  return (
    <div className="mobile-tab-content">
      <div className="mobile-chat-header">
        <span className="brand" style={{ fontSize: "16px" }}>RHCLOUD V1</span>
        <span style={{ fontSize: "12px", color: "var(--muted)", fontFamily: "var(--mono)" }}>AI 智能接力</span>
      </div>

      {running && (
        <div className="mobile-running-card" onClick={onGoToTask}>
          <div className="mobile-running-info">
            <span className="tab-pulse-dot" style={{ position: "static", display: "inline-block" }} />
            <strong>AI 接力正在协同运行中...</strong>
          </div>
          <span style={{ fontSize: "12px", color: "var(--accent)" }}>查看详情 ›</span>
        </div>
      )}

      {/* ── 磁盘满/排队提示 ── */}
      {concurrentBusy && (
        <div className="mobile-queue-banner">
          <div className="mobile-queue-banner-top">
            <span className="mobile-queue-icon">⏳</span>
            <div>
              <div className="mobile-queue-title">排队人数较多</div>
              <div className="mobile-queue-sub">
                {skipQueued
                  ? countdown > 0
                    ? `正在清理磁盘，${countdown}s 后自动重试...`
                    : "清理完成，正在为您插队重试…"
                  : "系统正在清理磁盘空间，点击插队可优先为您重试"}
              </div>
            </div>
          </div>

          {!skipQueued ? (
            <button
              className="mobile-queue-skip-btn"
              onClick={handleSkipQueue}
            >
              🚀 立即插队
            </button>
          ) : (
            <div className="mobile-queue-countdown">
              {countdown > 0 ? (
                <>
                  <svg className="mobile-queue-ring" viewBox="0 0 36 36">
                    <circle cx="18" cy="18" r="15.9" fill="none" stroke="var(--surface-2, #2a2a4a)" strokeWidth="3" />
                    <circle
                      cx="18" cy="18" r="15.9" fill="none"
                      stroke="var(--accent)" strokeWidth="3"
                      strokeDasharray={`${(countdown / 30) * 100} 100`}
                      strokeLinecap="round"
                      transform="rotate(-90 18 18)"
                    />
                  </svg>
                  <span className="mobile-queue-countdown-num">{countdown}</span>
                </>
              ) : (
                <span style={{ fontSize: "20px" }}>✅</span>
              )}
            </div>
          )}
        </div>
      )}


      <div className="mobile-card">
        <label className="field" htmlFor="mobile-q">
          输入您的任务或提问 (Prompt)
        </label>
        <textarea
          id="mobile-q"
          rows={4}
          placeholder="例如：帮我设计一个高并发短链服务架构..."
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          disabled={running}
          style={{ width: "100%", marginBottom: "12px" }}
        />

        <div className="mobile-model-summary" onClick={onGoToSettings}>
          <span style={{ fontSize: "12px", color: "var(--muted)" }}>参与模型 ({activeModels.length}):</span>
          <div className="mobile-model-logos">
            {activeModels.slice(0, 5).map((m) => (
              <AgentLogo key={m.id} provider={m.site} size={16} />
            ))}
            {activeModels.length > 5 && (
              <span style={{ fontSize: "11px", color: "var(--muted)" }}>+{activeModels.length - 5}</span>
            )}
          </div>
          <span style={{ fontSize: "12px", color: "var(--accent)", marginLeft: "auto" }}>修改 ›</span>
        </div>

        <div style={{ marginTop: "16px", display: "flex", gap: "10px" }}>
          <button
            id="mobile-start-btn"
            onClick={() => { run(); onGoToTask(); }}
            disabled={running || !question.trim()}
            style={{ flex: 1, height: "46px" }}
          >
            {running ? "🚀 协同中…" : "🚀 开始接力执行"}
          </button>
          {running && (
            <button
              className="ghost"
              onClick={cancel}
              style={{ color: "var(--danger)", borderColor: "var(--danger)", height: "46px" }}
            >
              🛑 终止
            </button>
          )}
        </div>
      </div>

      {chatHistory.length > 0 && (
        <div className="mobile-card" style={{ marginTop: "16px", borderLeft: "3px solid var(--border)" }}>
          <h4 style={{ margin: "0 0 10px 0", fontSize: "12px", fontWeight: 700, color: "var(--muted)", letterSpacing: "0.8px", textTransform: "uppercase" }}>历史记录</h4>
          <div className="mobile-history-list">
            {chatHistory.map((item) => (
              <SwipeableHistoryItem
                key={item.jobId}
                item={item}
                onDelete={handleDelete}
                onRetry={handleRetry}
                onLoad={handleLoad}
                onGoToTask={onGoToTask}
              />
            ))}
          </div>
        </div>
      )}

      {/* Scoped styles for swipeable history items */}
      <style>{`
        .swipeable-history-wrapper {
          position: relative;
          overflow: hidden;
          border-radius: 0;
        }

        .swipeable-delete-zone {
          position: absolute;
          right: 0;
          top: 0;
          bottom: 0;
          width: ${SWIPE_THRESHOLD}px;
          background: var(--danger, #e74c3c);
          color: #fff;
          display: flex;
          align-items: center;
          justify-content: center;
          font-size: 12px;
          font-weight: 700;
          cursor: pointer;
          border-radius: 0;
          user-select: none;
          letter-spacing: 0.3px;
        }

        .swipeable-foreground {
          position: relative;
          z-index: 1;
          background: var(--bg);
          will-change: transform;
        }

        .history-status-icon {
          margin-right: 6px;
          font-size: 13px;
          flex-shrink: 0;
        }

        .mobile-history-q {
          display: flex;
          align-items: flex-start;
        }
      `}</style>
    </div>
  );
});

ChatTab.displayName = "ChatTab";
