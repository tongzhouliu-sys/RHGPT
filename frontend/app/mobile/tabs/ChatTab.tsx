"use client";

import React, { useRef, useState, useCallback } from "react";
import { useAppJob, useAppSettings } from "../../context/AppContext";
import type { ChatHistoryItem } from "../../context/AppContext";
import AgentLogo from "../../components/AgentLogo";

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
  } = useAppJob();
  const { availableModels, selectedModels } = useAppSettings();

  const running = phase === "running";
  const activeModels = availableModels.filter((m) => selectedModels.includes(m.id));

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
        <div className="mobile-card" style={{ marginTop: "16px" }}>
          <h4 style={{ margin: "0 0 12px 0", fontSize: "14px", color: "var(--muted)" }}>🕒 历史记录</h4>
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
          border-radius: 8px;
          margin-bottom: 8px;
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
          font-size: 13px;
          font-weight: 600;
          cursor: pointer;
          border-radius: 0 8px 8px 0;
          user-select: none;
        }

        .swipeable-foreground {
          position: relative;
          z-index: 1;
          background: var(--surface, #1a1a2e);
          will-change: transform;
        }

        .history-status-icon {
          margin-right: 6px;
          font-size: 14px;
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
