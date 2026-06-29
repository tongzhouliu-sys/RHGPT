"use client";

import React from "react";

interface SystemWorkflowStepsProps {
  onGoToChat: () => void;
}

const STEPS = [
  {
    num: "01",
    title: "初稿生成 (Generate)",
    subtitle: "多模型并发竞速赛马",
    desc: "选中的所有大模型同时触发生成，系统实时监控首 Token 吐字速度，最快者锁定领跑并输出初始基准方案。",
    icon: "⚡",
    tag: "首 Token 竞速",
    color: "var(--accent, #6366f1)"
  },
  {
    num: "02",
    title: "交叉评审 (Review)",
    subtitle: "多 Agent 盲审与漏洞检测",
    desc: "协同模型针对第一步生成的初稿进行多视角审阅，检测潜在的逻辑漏洞、边界条件与改进空间。",
    icon: "🔍",
    tag: "多维交叉质控",
    color: "#ec4899"
  },
  {
    num: "03",
    title: "逻辑拆解 (Deep Analyze)",
    subtitle: "核心难题深度推理与解构",
    desc: "将复杂需求进一步拆解为系统化的组件与算法逻辑，补全缺失的关键技术细节与架构推演。",
    icon: "🧩",
    tag: "算法深度推演",
    color: "#8b5cf6"
  },
  {
    num: "04",
    title: "方案优化 (Improve)",
    subtitle: "工程级代码与策略调优",
    desc: "结合评审意见与拆解分析，对整体方案进行二次重构与迭代优化，确保代码的高可用与高性能。",
    icon: "🛠️",
    tag: "方案二次重构",
    color: "#10b981"
  },
  {
    num: "05",
    title: "总结收尾 (Summary)",
    subtitle: "结构化成果聚合导出",
    desc: "整合前 4 轮接力的所有智慧结晶，剔除冗余，归纳并输出结构清晰、可直接落地的 Markdown 最终交付件。",
    icon: "📦",
    tag: "终极成果交付",
    color: "#f59e0b"
  }
];

export const SystemWorkflowSteps: React.FC<SystemWorkflowStepsProps> = React.memo(({ onGoToChat }) => {
  return (
    <div className="workflow-container">
      {/* 头部介绍 */}
      <div className="workflow-header">
        <div className="workflow-badge">⚙️ 系统协同架构</div>
        <h2 className="workflow-title">AI 多模型接力运行流程</h2>
        <p className="workflow-subtitle">
          在 RHCLOUD 中，每一个任务都将经历 5 轮严密的多模型协同竞速与接力推理。以下为系统自动化运行的标准步骤：
        </p>
      </div>

      {/* 步骤时间轴列表 */}
      <div className="workflow-steps-list">
        {STEPS.map((step, index) => (
          <div key={step.num} className="workflow-step-card">
            <div className="step-card-header">
              <div className="step-num-badge" style={{ backgroundColor: `${step.color}20`, color: step.color, borderColor: `${step.color}40` }}>
                STEP {step.num}
              </div>
              <div className="step-icon-wrap" style={{ background: `${step.color}15`, color: step.color }}>
                {step.icon}
              </div>
            </div>
            <div className="step-card-body">
              <div className="step-title-row">
                <h3 className="step-title">{step.title}</h3>
                <span className="step-tag" style={{ color: step.color, backgroundColor: `${step.color}12` }}>
                  {step.tag}
                </span>
              </div>
              <div className="step-subtitle">{step.subtitle}</div>
              <p className="step-desc">{step.desc}</p>
            </div>

            {/* 连接线 (最后一个节点不加) */}
            {index < STEPS.length - 1 && <div className="step-connector-line" />}
          </div>
        ))}
      </div>

      {/* 核心亮点说明卡片 */}
      <div className="workflow-highlights">
        <div className="highlight-item">
          <span className="highlight-icon">⚡</span>
          <div>
            <strong>全步骤并发竞速赛马</strong>
            <p>选中的模型同台竞技，首 Token 吐字最快者自动锁定获胜，避免单模型卡顿等待。</p>
          </div>
        </div>
        <div className="highlight-item">
          <span className="highlight-icon">🔄</span>
          <div>
            <strong>透明流式秒级刷屏</strong>
            <p>全流程透明化展示，节点接力过程与中间思考实时呈现在任务面板中。</p>
          </div>
        </div>
      </div>

      {/* 底部 CTA 去发起任务 */}
      <div className="workflow-cta-box">
        <p>了解完系统接力机制？现在就体验 AI 团队协同的强大力量吧！</p>
        <button type="button" className="workflow-cta-btn" onClick={onGoToChat}>
          💬 立即去发起接力任务
        </button>
      </div>
    </div>
  );
});

SystemWorkflowSteps.displayName = "SystemWorkflowSteps";
