import React, { useState, useEffect, useRef } from "react";
import { motion, AnimatePresence } from "framer-motion";
import {
  Terminal,
  Copy,
  Check,
  Cpu,
  Shield,
  Zap,
  Sparkles,
  Layers,
  DollarSign,
  Database,
  GitBranch,
  ArrowRight,
  ExternalLink,
  Activity,
  Users,
  Lock,
  ChevronDown,
  CheckCircle2,
  Sliders,
  Play,
  RefreshCw,
  Star,
  Code,
  Compass,
  ArrowUpRight
} from "lucide-react";

export default function App() {
  const [copiedInstall, setCopiedInstall] = useState(false);
  const [activeInstallTab, setActiveInstallTab] = useState("npm");
  const [annualBilling, setAnnualBilling] = useState(true);

  // ROI Calculator State
  const [devCount, setDevCount] = useState(8);
  const [monthlySpendPerDev, setMonthlySpendPerDev] = useState(250);

  // Cross-client Continuity Simulator State
  const [activeStep, setActiveStep] = useState(0);

  // Canvas Ref for Interactive Orbit Visualizer
  const canvasRef = useRef(null);

  // Copy helper
  const handleCopyInstall = (text) => {
    navigator.clipboard.writeText(text);
    setCopiedInstall(true);
    setTimeout(() => setCopiedInstall(false), 2000);
  };

  // ROI calculations
  const totalMonthlySpend = devCount * monthlySpendPerDev;
  const savingsPct = 0.52; // 52% average token reduction
  const monthlySavings = Math.round(totalMonthlySpend * savingsPct);
  const annualSavings = monthlySavings * 12;
  const tokensSavedMillions = Math.round((monthlySavings / 0.003) / 1000) / 1000; // estimated on $3/Mtok blended

  // Continuity simulation steps
  const simulationSteps = [
    {
      id: "opencode",
      client: "OpenCode",
      role: "Architecture & Planning",
      command: 'genesis dialogue_update --topic "Zero-Copy Streaming Proxy"',
      state: "Solidifying architectural invariants in subconscious memory...",
      capsule: "• [Active Thread]: Zero-Copy Streaming Proxy (4 invariants verified)\n• [Solidified Rules]: Rule 31 (SQLite WAL), Rule 32 (Spool Pointers)\n• [Resolution Order]: capsule first → dialogue_buffer → recall",
      badgeColor: "#38BDF8",
    },
    {
      id: "cursor",
      client: "Cursor",
      role: "Core Implementation",
      command: 'genesis hook --cursor (Subconscious hook injected under 176 tok)',
      state: "Instantly inherits OpenCode thread context without manual prompting",
      capsule: "• [Subconscious Capsule]: Continues proxy_server.py implementation\n• [Tool Compactor]: git diff compacted 74% (12k tok → 2.8k tok)\n• [Prompt Diet]: Redacted 2 secret bearer tokens before persistence",
      badgeColor: "#818CF8",
    },
    {
      id: "claude",
      client: "Claude Code",
      role: "Headless Test & Verify",
      command: "genesis run -- pytest tests/test_genesis_proxy.py",
      state: "Spools 4,200 lines of test output into 80-token locked summary",
      capsule: "• [Lossless Spool]: pytest tests/test_genesis_proxy.py (100% pass)\n• [Hebbian Trace]: Strengthened proxy ↔ streaming engram link +0.15\n• [Token Saved]: 28,400 tokens preserved for reasoning",
      badgeColor: "#22C55E",
    },
    {
      id: "antigravity",
      client: "Antigravity IDE",
      role: "Observability & Review",
      command: "genesis dashboard --port 8090",
      state: "Interactive Orbit Graph visualizes memory clusters and active engrams",
      capsule: "• [Telemetry]: 225/225 tests certified, 42.1% net token savings\n• [Sleep Daemon]: Queued 3 ephemeral nodes for overnight distillation\n• [Audit]: Zero secrets leaked, fail-open bypass verified",
      badgeColor: "#F59E0B",
    }
  ];

  // Interactive Canvas Memory Orbit Simulation
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    let animationFrameId;

    let width = (canvas.width = canvas.parentElement.clientWidth);
    let height = (canvas.height = 360);

    const handleResize = () => {
      if (!canvas.parentElement) return;
      width = canvas.width = canvas.parentElement.clientWidth;
      height = canvas.height = 360;
    };
    window.addEventListener("resize", handleResize);

    // Memory nodes
    const nodeCount = 28;
    const nodes = [];
    const colors = ["#38BDF8", "#22C55E", "#818CF8", "#F59E0B"];

    for (let i = 0; i < nodeCount; i++) {
      const orbitTier = (i % 3) + 1; // 1: Core, 2: Active, 3: Consolidated
      nodes.push({
        orbitRadius: orbitTier * 50 + Math.random() * 30,
        angle: Math.random() * Math.PI * 2,
        speed: (0.003 + Math.random() * 0.006) * (orbitTier === 2 ? -1 : 1),
        size: 3 + Math.random() * 4,
        color: colors[i % colors.length],
        label: i === 0 ? "Active Thread" : i === 1 ? "Rule 31 (WAL)" : i === 2 ? "Tool Compactor" : null,
      });
    }

    let centerX = width / 2;
    let centerY = height / 2;

    const render = () => {
      centerX = width / 2;
      centerY = height / 2;
      ctx.clearRect(0, 0, width, height);

      // Draw Orbit Rings
      [65, 115, 165].forEach((r, idx) => {
        ctx.beginPath();
        ctx.arc(centerX, centerY, r, 0, Math.PI * 2);
        ctx.strokeStyle = idx === 0 ? "rgba(56, 189, 248, 0.25)" : "rgba(71, 85, 105, 0.2)";
        ctx.lineWidth = 1;
        ctx.setLineDash([4, 6]);
        ctx.stroke();
        ctx.setLineDash([]);
      });

      // Draw Center Nucleus (GENESIS Brain)
      ctx.beginPath();
      ctx.arc(centerX, centerY, 14, 0, Math.PI * 2);
      ctx.fillStyle = "#38BDF8";
      ctx.shadowColor = "#38BDF8";
      ctx.shadowBlur = 20;
      ctx.fill();
      ctx.shadowBlur = 0;

      // Update and Draw Nodes
      nodes.forEach((n) => {
        n.angle += n.speed;
        const x = centerX + Math.cos(n.angle) * n.orbitRadius;
        const y = centerY + Math.sin(n.angle) * n.orbitRadius;

        // Gravitational link line to center
        ctx.beginPath();
        ctx.moveTo(centerX, centerY);
        ctx.lineTo(x, y);
        ctx.strokeStyle = "rgba(56, 189, 248, 0.06)";
        ctx.lineWidth = 0.8;
        ctx.stroke();

        // Node circle
        ctx.beginPath();
        ctx.arc(x, y, n.size, 0, Math.PI * 2);
        ctx.fillStyle = n.color;
        ctx.shadowColor = n.color;
        ctx.shadowBlur = 8;
        ctx.fill();
        ctx.shadowBlur = 0;

        // Text label
        if (n.label) {
          ctx.fillStyle = "#E2E8F0";
          ctx.font = "10px JetBrains Mono";
          ctx.fillText(n.label, x + 8, y + 3);
        }
      });

      animationFrameId = requestAnimationFrame(render);
    };

    render();

    return () => {
      cancelAnimationFrame(animationFrameId);
      window.removeEventListener("resize", handleResize);
    };
  }, []);

  const installCommands = {
    npm: "npm install -g genesis-memory && genesis setup",
    brew: "brew install genesis-memory && genesis setup",
    pip: "pip install genesis-memory && python -m genesis_memory.cli.run setup",
  };

  return (
    <div style={{ minHeight: "100vh", display: "flex", flexDirection: "column" }}>
      {/* =====================================================================
          1. STICKY TOP NAVBAR
          ===================================================================== */}
      <header
        style={{
          position: "sticky",
          top: 0,
          zIndex: 50,
          backgroundColor: "rgba(7, 9, 14, 0.85)",
          backdropFilter: "blur(16px)",
          borderBottom: "1px solid var(--border-subtle)",
        }}
      >
        <div
          className="container"
          style={{
            height: "72px",
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
          }}
        >
          {/* Brand Logo */}
          <div style={{ display: "flex", alignItems: "center", gap: "12px" }}>
            <div
              style={{
                width: "36px",
                height: "36px",
                borderRadius: "10px",
                background: "linear-gradient(135deg, #38BDF8 0%, #2563EB 100%)",
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                boxShadow: "0 0 16px rgba(56, 189, 248, 0.4)",
              }}
            >
              <Cpu size={20} color="#07090E" />
            </div>
            <div>
              <span style={{ fontWeight: 800, fontSize: "18px", letterSpacing: "-0.5px" }}>
                GENESIS<span style={{ color: "var(--accent-cyan)" }}>.memory</span>
              </span>
            </div>
            <div className="badge" style={{ marginLeft: "8px" }}>
              <span className="badge-dot"></span>
              <span>v0.4.0 Engine</span>
            </div>
          </div>

          {/* Navigation Links */}
          <nav style={{ display: "none", alignItems: "center", gap: "28px" }} className="desktop-nav">
            <a href="#features" style={{ color: "var(--text-secondary)", textDecoration: "none", fontSize: "14px", fontWeight: 500 }}>
              Features
            </a>
            <a href="#continuity" style={{ color: "var(--text-secondary)", textDecoration: "none", fontSize: "14px", fontWeight: 500 }}>
              Multi-Client Simulator
            </a>
            <a href="#calculator" style={{ color: "var(--text-secondary)", textDecoration: "none", fontSize: "14px", fontWeight: 500 }}>
              Token ROI
            </a>
            <a href="#architecture" style={{ color: "var(--text-secondary)", textDecoration: "none", fontSize: "14px", fontWeight: 500 }}>
              Architecture
            </a>
            <a href="#pricing" style={{ color: "var(--text-secondary)", textDecoration: "none", fontSize: "14px", fontWeight: 500 }}>
              Pricing
            </a>
          </nav>

          {/* Right Action CTA */}
          <div style={{ display: "flex", alignItems: "center", gap: "12px" }}>
            <a
              href="https://github.com/HamidRezaeian/genesis-memory"
              target="_blank"
              rel="noreferrer"
              className="btn btn-secondary"
              style={{ padding: "8px 14px", fontSize: "13px" }}
            >
              <Star size={15} color="#F59E0B" />
              <span>GitHub</span>
            </a>
            <a href="#install" className="btn btn-primary" style={{ padding: "8px 18px", fontSize: "13px" }}>
              <span>1-Click Setup</span>
              <ArrowRight size={15} />
            </a>
          </div>
        </div>
      </header>

      {/* =====================================================================
          2. HERO SECTION
          ===================================================================== */}
      <section style={{ paddingTop: "72px", paddingBottom: "80px", position: "relative" }}>
        <div className="container" style={{ textAlign: "center", maxWidth: "980px" }}>
          <motion.div
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.5 }}
            style={{ display: "inline-flex", marginBottom: "20px" }}
          >
            <div className="badge" style={{ padding: "6px 16px", borderColor: "rgba(56, 189, 248, 0.4)" }}>
              <Sparkles size={14} color="#38BDF8" />
              <span style={{ color: "#E2E8F0" }}>Autonomous Cognitive Memory & 50%+ Token Compactor</span>
            </div>
          </motion.div>

          <motion.h1
            initial={{ opacity: 0, y: 25 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.6, delay: 0.1 }}
            style={{
              fontSize: "clamp(36px, 5.5vw, 64px)",
              fontWeight: 800,
              lineHeight: 1.12,
              letterSpacing: "-1.5px",
              marginBottom: "24px",
            }}
          >
            The Persistent Brain & Token Diet for{" "}
            <span className="gradient-text-cyan">AI Coding Agents</span>
          </motion.h1>

          <motion.p
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.6, delay: 0.2 }}
            style={{
              fontSize: "clamp(16px, 2vw, 20px)",
              color: "var(--text-secondary)",
              lineHeight: 1.6,
              maxWidth: "780px",
              margin: "0 auto 40px",
            }}
          >
            Stop repeating project context across tools. Seamlessly switch between{" "}
            <strong style={{ color: "#FFF" }}>Cursor</strong>, <strong style={{ color: "#FFF" }}>Claude Code</strong>,{" "}
            <strong style={{ color: "#FFF" }}>OpenCode</strong>, and <strong style={{ color: "#FFF" }}>Antigravity</strong>{" "}
            while cutting your team's monthly LLM API token bills by 40% to 70%.
          </motion.p>

          {/* Interactive Install Terminal Box */}
          <motion.div
            id="install"
            initial={{ opacity: 0, scale: 0.96 }}
            animate={{ opacity: 1, scale: 1 }}
            transition={{ duration: 0.6, delay: 0.3 }}
            style={{ maxWidth: "640px", margin: "0 auto 48px", textAlign: "left" }}
          >
            <div
              style={{
                backgroundColor: "#0A0E18",
                border: "1px solid var(--border-subtle)",
                borderRadius: "var(--radius-lg)",
                overflow: "hidden",
                boxShadow: "0 16px 40px rgba(0, 0, 0, 0.6)",
              }}
            >
              {/* Terminal Window Bar */}
              <div
                style={{
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "space-between",
                  padding: "10px 16px",
                  borderBottom: "1px solid var(--border-subtle)",
                  backgroundColor: "#0F1422",
                }}
              >
                <div style={{ display: "flex", alignItems: "center", gap: "6px" }}>
                  <div style={{ width: "10px", height: "10px", borderRadius: "50%", backgroundColor: "#EF4444" }}></div>
                  <div style={{ width: "10px", height: "10px", borderRadius: "50%", backgroundColor: "#F59E0B" }}></div>
                  <div style={{ width: "10px", height: "10px", borderRadius: "50%", backgroundColor: "#22C55E" }}></div>
                  <span style={{ fontSize: "12px", color: "var(--text-tertiary)", marginLeft: "10px", fontFamily: "var(--font-mono)" }}>
                    bash — genesis-setup
                  </span>
                </div>

                <div style={{ display: "flex", gap: "6px" }}>
                  {["npm", "brew", "pip"].map((pkg) => (
                    <button
                      key={pkg}
                      onClick={() => setActiveInstallTab(pkg)}
                      style={{
                        padding: "2px 8px",
                        fontSize: "11px",
                        fontFamily: "var(--font-mono)",
                        borderRadius: "4px",
                        border: "none",
                        cursor: "pointer",
                        background: activeInstallTab === pkg ? "rgba(56, 189, 248, 0.2)" : "transparent",
                        color: activeInstallTab === pkg ? "var(--accent-cyan)" : "var(--text-tertiary)",
                      }}
                    >
                      {pkg}
                    </button>
                  ))}
                </div>
              </div>

              {/* Command row */}
              <div style={{ padding: "16px 20px", display: "flex", alignItems: "center", justifyContent: "space-between" }}>
                <div style={{ display: "flex", alignItems: "center", gap: "10px", overflow: "hidden" }}>
                  <span style={{ color: "var(--accent-cyan)", fontFamily: "var(--font-mono)", fontWeight: 700 }}>$</span>
                  <span style={{ fontFamily: "var(--font-mono)", fontSize: "14px", color: "#F8FAFC", wordBreak: "break-all" }}>
                    {installCommands[activeInstallTab]}
                  </span>
                </div>
                <button
                  onClick={() => handleCopyInstall(installCommands[activeInstallTab])}
                  className="btn btn-secondary"
                  style={{ padding: "6px 12px", fontSize: "12px", flexShrink: 0, marginLeft: "12px" }}
                >
                  {copiedInstall ? (
                    <>
                      <Check size={14} color="#22C55E" />
                      <span style={{ color: "#22C55E" }}>Copied!</span>
                    </>
                  ) : (
                    <>
                      <Copy size={14} />
                      <span>Copy</span>
                    </>
                  )}
                </button>
              </div>
            </div>
          </motion.div>

          {/* Social Proof & Metrics Badges */}
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            transition={{ duration: 0.6, delay: 0.4 }}
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(auto-fit, minmax(190px, 1fr))",
              gap: "16px",
            }}
          >
            <div className="glass-panel" style={{ padding: "16px 20px", textAlign: "left" }}>
              <div style={{ display: "flex", alignItems: "center", gap: "8px", color: "var(--accent-cyan)", marginBottom: "4px" }}>
                <CheckCircle2 size={16} />
                <span className="font-mono" style={{ fontSize: "18px", fontWeight: 700 }}>225 / 225</span>
              </div>
              <p style={{ fontSize: "13px", color: "var(--text-secondary)" }}>Certified Test Pass Rate (100%)</p>
            </div>

            <div className="glass-panel" style={{ padding: "16px 20px", textAlign: "left" }}>
              <div style={{ display: "flex", alignItems: "center", gap: "8px", color: "var(--accent-emerald)", marginBottom: "4px" }}>
                <Zap size={16} />
                <span className="font-mono" style={{ fontSize: "18px", fontWeight: 700 }}>176 Tokens</span>
              </div>
              <p style={{ fontSize: "13px", color: "var(--text-secondary)" }}>Strict Hook Diet (Budget &lt; 200)</p>
            </div>

            <div className="glass-panel" style={{ padding: "16px 20px", textAlign: "left" }}>
              <div style={{ display: "flex", alignItems: "center", gap: "8px", color: "var(--accent-violet)", marginBottom: "4px" }}>
                <Layers size={16} />
                <span className="font-mono" style={{ fontSize: "18px", fontWeight: 700 }}>60% - 80%</span>
              </div>
              <p style={{ fontSize: "13px", color: "var(--text-secondary)" }}>Tool Output Compression</p>
            </div>

            <div className="glass-panel" style={{ padding: "16px 20px", textAlign: "left" }}>
              <div style={{ display: "flex", alignItems: "center", gap: "8px", color: "#F59E0B", marginBottom: "4px" }}>
                <Shield size={16} />
                <span className="font-mono" style={{ fontSize: "18px", fontWeight: 700 }}>Zero-Leak</span>
              </div>
              <p style={{ fontSize: "13px", color: "var(--text-secondary)" }}>Air-Gapped Secrets Redaction</p>
            </div>
          </motion.div>
        </div>
      </section>

      {/* =====================================================================
          3. INTERACTIVE CROSS-TOOL MEMORY CONTINUITY SIMULATOR
          ===================================================================== */}
      <section id="continuity" style={{ padding: "80px 0", borderTop: "1px solid var(--border-subtle)" }}>
        <div className="container">
          <div style={{ textAlign: "center", maxWidth: "700px", margin: "0 auto 48px" }}>
            <div className="badge" style={{ marginBottom: "12px" }}>
              <GitBranch size={14} color="#818CF8" />
              <span>Multi-Client Unified Working Memory</span>
            </div>
            <h2 style={{ fontSize: "36px", fontWeight: 800, letterSpacing: "-1px", marginBottom: "16px" }}>
              Zero-Friction Context Handoff Across Tools
            </h2>
            <p style={{ color: "var(--text-secondary)", fontSize: "16px" }}>
              Watch how GENESIS preserves your active thoughts, rule decisions, and thread state when you move from planning to coding to testing.
            </p>
          </div>

          {/* Step Selector Tabs */}
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(4, 1fr)",
              gap: "12px",
              marginBottom: "24px",
            }}
          >
            {simulationSteps.map((step, index) => (
              <button
                key={step.id}
                onClick={() => setActiveStep(index)}
                style={{
                  padding: "14px 16px",
                  borderRadius: "var(--radius-md)",
                  border: activeStep === index ? `1px solid ${step.badgeColor}` : "1px solid var(--border-subtle)",
                  backgroundColor: activeStep === index ? "rgba(17, 24, 39, 0.9)" : "rgba(17, 24, 39, 0.4)",
                  textAlign: "left",
                  cursor: "pointer",
                  transition: "all 200ms ease",
                  boxShadow: activeStep === index ? `0 0 20px ${step.badgeColor}22` : "none",
                }}
              >
                <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: "6px" }}>
                  <span style={{ fontSize: "12px", fontWeight: 700, color: step.badgeColor }}>STEP {index + 1}</span>
                  <span
                    style={{
                      width: "8px",
                      height: "8px",
                      borderRadius: "50%",
                      backgroundColor: activeStep === index ? step.badgeColor : "transparent",
                    }}
                  ></span>
                </div>
                <div style={{ fontWeight: 700, fontSize: "15px", color: "#FFF", marginBottom: "2px" }}>{step.client}</div>
                <div style={{ fontSize: "12px", color: "var(--text-tertiary)" }}>{step.role}</div>
              </button>
            ))}
          </div>

          {/* Simulated Active State Display */}
          <AnimatePresence mode="wait">
            <motion.div
              key={activeStep}
              initial={{ opacity: 0, y: 10 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -10 }}
              transition={{ duration: 0.3 }}
              className="glass-panel"
              style={{ padding: "28px", borderLeft: `4px solid ${simulationSteps[activeStep].badgeColor}` }}
            >
              <div style={{ display: "flex", flexWrap: "wrap", alignItems: "center", justifyContent: "space-between", gap: "16px", marginBottom: "20px" }}>
                <div>
                  <div style={{ fontSize: "13px", color: simulationSteps[activeStep].badgeColor, fontWeight: 600, textTransform: "uppercase" }}>
                    Active Client Environment
                  </div>
                  <h3 style={{ fontSize: "22px", fontWeight: 700, color: "#FFF" }}>
                    {simulationSteps[activeStep].client} — {simulationSteps[activeStep].role}
                  </h3>
                </div>
                <div className="badge" style={{ borderColor: "var(--border-light)" }}>
                  <Activity size={14} color="#22C55E" />
                  <span>Subconscious Hook Active</span>
                </div>
              </div>

              <div style={{ marginBottom: "20px" }}>
                <div style={{ fontSize: "12px", color: "var(--text-secondary)", marginBottom: "6px", fontFamily: "var(--font-mono)" }}>
                  TRIGGER / EXECUTION:
                </div>
                <div className="code-box" style={{ color: "#7DD3FC" }}>
                  <span>{simulationSteps[activeStep].command}</span>
                  <Play size={14} color="#38BDF8" />
                </div>
              </div>

              <div>
                <div style={{ fontSize: "12px", color: "var(--text-secondary)", marginBottom: "6px", fontFamily: "var(--font-mono)" }}>
                  INJECTED WORKING MEMORY CAPSULE (EXP108 CERTIFIED):
                </div>
                <pre
                  style={{
                    backgroundColor: "#06090F",
                    border: "1px solid var(--border-subtle)",
                    borderRadius: "var(--radius-sm)",
                    padding: "16px",
                    fontFamily: "var(--font-mono)",
                    fontSize: "13px",
                    color: "#A7F3D0",
                    whiteSpace: "pre-wrap",
                    lineHeight: 1.6,
                  }}
                >
                  {simulationSteps[activeStep].capsule}
                </pre>
              </div>
            </motion.div>
          </AnimatePresence>
        </div>
      </section>

      {/* =====================================================================
          4. LIVE TOKEN SAVINGS & ROI CALCULATOR
          ===================================================================== */}
      <section id="calculator" style={{ padding: "80px 0", backgroundColor: "rgba(11, 15, 23, 0.6)" }}>
        <div className="container">
          <div style={{ textAlign: "center", maxWidth: "700px", margin: "0 auto 48px" }}>
            <div className="badge" style={{ marginBottom: "12px" }}>
              <DollarSign size={14} color="#22C55E" />
              <span>Measurable Bottom-Line ROI</span>
            </div>
            <h2 style={{ fontSize: "36px", fontWeight: 800, letterSpacing: "-1px", marginBottom: "16px" }}>
              Calculate Your Team's Token Savings
            </h2>
            <p style={{ color: "var(--text-secondary)", fontSize: "16px" }}>
              Tool compactor and strict subconscious budget eliminate thousands of redundant tokens per interaction without sacrificing model accuracy.
            </p>
          </div>

          <div
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(auto-fit, minmax(320px, 1fr))",
              gap: "32px",
              alignItems: "center",
            }}
          >
            {/* Slider Controls */}
            <div className="glass-panel" style={{ padding: "32px" }}>
              {/* Dev count slider */}
              <div style={{ marginBottom: "32px" }}>
                <div style={{ display: "flex", justifyContent: "space-between", marginBottom: "12px" }}>
                  <label style={{ fontSize: "15px", fontWeight: 600, color: "#F8FAFC" }}>Developers in Your Team</label>
                  <span className="font-mono" style={{ fontSize: "18px", fontWeight: 700, color: "var(--accent-cyan)" }}>
                    {devCount} devs
                  </span>
                </div>
                <input
                  type="range"
                  min="1"
                  max="50"
                  value={devCount}
                  onChange={(e) => setDevCount(Number(e.target.value))}
                />
                <div style={{ display: "flex", justifyContent: "space-between", marginTop: "6px", fontSize: "12px", color: "var(--text-tertiary)" }}>
                  <span>1 Solo Dev</span>
                  <span>25 Team</span>
                  <span>50 Enterprise</span>
                </div>
              </div>

              {/* Monthly spend per dev */}
              <div style={{ marginBottom: "24px" }}>
                <div style={{ display: "flex", justifyContent: "space-between", marginBottom: "12px" }}>
                  <label style={{ fontSize: "15px", fontWeight: 600, color: "#F8FAFC" }}>Monthly API Spend per Dev</label>
                  <span className="font-mono" style={{ fontSize: "18px", fontWeight: 700, color: "var(--accent-emerald)" }}>
                    ${monthlySpendPerDev} / mo
                  </span>
                </div>
                <input
                  type="range"
                  min="50"
                  max="600"
                  step="25"
                  value={monthlySpendPerDev}
                  onChange={(e) => setMonthlySpendPerDev(Number(e.target.value))}
                />
                <div style={{ display: "flex", justifyContent: "space-between", marginTop: "6px", fontSize: "12px", color: "var(--text-tertiary)" }}>
                  <span>$50</span>
                  <span>$300</span>
                  <span>$600+</span>
                </div>
              </div>

              <div style={{ fontSize: "13px", color: "var(--text-secondary)", lineHeight: 1.5 }}>
                Based on production benchmark data: 72% tool output compression, 176-token subconscious injection ceiling, and headless pytest/git spooling.
              </div>
            </div>

            {/* Savings Display Cards */}
            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))", gap: "16px" }}>
              <div
                className="glass-panel"
                style={{
                  padding: "24px",
                  backgroundColor: "rgba(34, 197, 94, 0.08)",
                  borderColor: "rgba(34, 197, 94, 0.3)",
                }}
              >
                <div style={{ fontSize: "13px", color: "#86EFAC", fontWeight: 600, marginBottom: "8px" }}>
                  ESTIMATED MONTHLY SAVINGS
                </div>
                <div className="font-mono" style={{ fontSize: "36px", fontWeight: 800, color: "#22C55E" }}>
                  ${monthlySavings.toLocaleString()}
                </div>
                <div style={{ fontSize: "12px", color: "var(--text-secondary)", marginTop: "4px" }}>
                  ${(totalMonthlySpend - monthlySavings).toLocaleString()} net bill (52% cut)
                </div>
              </div>

              <div
                className="glass-panel"
                style={{
                  padding: "24px",
                  backgroundColor: "rgba(56, 189, 248, 0.08)",
                  borderColor: "rgba(56, 189, 248, 0.3)",
                }}
              >
                <div style={{ fontSize: "13px", color: "#BAE6FD", fontWeight: 600, marginBottom: "8px" }}>
                  ANNUAL TEAM SAVINGS
                </div>
                <div className="font-mono" style={{ fontSize: "36px", fontWeight: 800, color: "#38BDF8" }}>
                  ${annualSavings.toLocaleString()}
                </div>
                <div style={{ fontSize: "12px", color: "var(--text-secondary)", marginTop: "4px" }}>
                  Direct company savings per year
                </div>
              </div>

              <div className="glass-panel" style={{ padding: "24px" }}>
                <div style={{ fontSize: "13px", color: "var(--text-secondary)", fontWeight: 600, marginBottom: "8px" }}>
                  TOKENS PRESERVED / MO
                </div>
                <div className="font-mono" style={{ fontSize: "28px", fontWeight: 800, color: "#FFF" }}>
                  ~{tokensSavedMillions.toFixed(1)}M
                </div>
                <div style={{ fontSize: "12px", color: "var(--text-tertiary)", marginTop: "4px" }}>
                  Keeps context windows fresh
                </div>
              </div>

              <div className="glass-panel" style={{ padding: "24px" }}>
                <div style={{ fontSize: "13px", color: "var(--text-secondary)", fontWeight: 600, marginBottom: "8px" }}>
                  PAYBACK PERIOD
                </div>
                <div className="font-mono" style={{ fontSize: "28px", fontWeight: 800, color: "#A855F7" }}>
                  &lt; 5 Days
                </div>
                <div style={{ fontSize: "12px", color: "var(--text-tertiary)", marginTop: "4px" }}>
                  Immediate positive ROI
                </div>
              </div>
            </div>
          </div>
        </div>
      </section>

      {/* =====================================================================
          5. INTERACTIVE MEMORY ORBIT VISUALIZER
          ===================================================================== */}
      <section id="architecture" style={{ padding: "80px 0", borderTop: "1px solid var(--border-subtle)" }}>
        <div className="container">
          <div style={{ textAlign: "center", maxWidth: "700px", margin: "0 auto 36px" }}>
            <div className="badge" style={{ marginBottom: "12px" }}>
              <Compass size={14} color="#38BDF8" />
              <span>Concentric Cognitive Architecture</span>
            </div>
            <h2 style={{ fontSize: "36px", fontWeight: 800, letterSpacing: "-1px", marginBottom: "16px" }}>
              Live Neural Memory Engine
            </h2>
            <p style={{ color: "var(--text-secondary)", fontSize: "16px" }}>
              Episodic engrams rotate through concentric gravity orbits: Core architectural rules stay anchored, while transient command logs decay or consolidate during sleep.
            </p>
          </div>

          <div
            className="glass-panel"
            style={{
              padding: "24px",
              position: "relative",
              overflow: "hidden",
            }}
          >
            <canvas ref={canvasRef} style={{ width: "100%", height: "360px", display: "block" }} />

            <div
              style={{
                position: "absolute",
                bottom: "20px",
                left: "20px",
                display: "flex",
                gap: "16px",
                background: "rgba(11, 15, 23, 0.8)",
                backdropFilter: "blur(12px)",
                padding: "8px 16px",
                borderRadius: "var(--radius-md)",
                border: "1px solid var(--border-subtle)",
                fontSize: "12px",
              }}
            >
              <div style={{ display: "flex", alignItems: "center", gap: "6px" }}>
                <span style={{ width: "8px", height: "8px", borderRadius: "50%", backgroundColor: "#38BDF8" }}></span>
                <span>Core Rules (Fixed)</span>
              </div>
              <div style={{ display: "flex", alignItems: "center", gap: "6px" }}>
                <span style={{ width: "8px", height: "8px", borderRadius: "50%", backgroundColor: "#818CF8" }}></span>
                <span>Active Threads (Dynamic)</span>
              </div>
              <div style={{ display: "flex", alignItems: "center", gap: "6px" }}>
                <span style={{ width: "8px", height: "8px", borderRadius: "50%", backgroundColor: "#22C55E" }}></span>
                <span>Consolidated Skills</span>
              </div>
            </div>
          </div>
        </div>
      </section>

      {/* =====================================================================
          6. FEATURES BENTO GRID
          ===================================================================== */}
      <section id="features" style={{ padding: "80px 0", backgroundColor: "rgba(11, 15, 23, 0.5)" }}>
        <div className="container">
          <div style={{ textAlign: "center", maxWidth: "700px", margin: "0 auto 48px" }}>
            <div className="badge" style={{ marginBottom: "12px" }}>
              <Layers size={14} color="#38BDF8" />
              <span>Full-Stack Engineering Invariants</span>
            </div>
            <h2 style={{ fontSize: "36px", fontWeight: 800, letterSpacing: "-1px", marginBottom: "16px" }}>
              Built for Professional Autonomous Workflows
            </h2>
            <p style={{ color: "var(--text-secondary)", fontSize: "16px" }}>
              Every feature is engineered to protect context window limits, maintain strict privacy, and keep LLMs reliable over long pairing sessions.
            </p>
          </div>

          <div
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(auto-fit, minmax(320px, 1fr))",
              gap: "20px",
            }}
          >
            {/* Bento Card 1 */}
            <div className="glass-panel" style={{ padding: "28px" }}>
              <div style={{ width: "40px", height: "40px", borderRadius: "10px", backgroundColor: "rgba(56, 189, 248, 0.15)", display: "flex", alignItems: "center", justifyContent: "center", marginBottom: "16px" }}>
                <Zap size={20} color="#38BDF8" />
              </div>
              <h3 style={{ fontSize: "18px", fontWeight: 700, color: "#FFF", marginBottom: "8px" }}>Subconscious Hook Diet</h3>
              <p style={{ fontSize: "14px", color: "var(--text-secondary)", lineHeight: 1.6 }}>
                EXP108 strict budget discipline: Memory injections never exceed 200 tokens. The agent gets essential recall, active threads, and rules without filling its context.
              </p>
            </div>

            {/* Bento Card 2 */}
            <div className="glass-panel" style={{ padding: "28px" }}>
              <div style={{ width: "40px", height: "40px", borderRadius: "10px", backgroundColor: "rgba(34, 197, 94, 0.15)", display: "flex", alignItems: "center", justifyContent: "center", marginBottom: "16px" }}>
                <Terminal size={20} color="#22C55E" />
              </div>
              <h3 style={{ fontSize: "18px", fontWeight: 700, color: "#FFF", marginBottom: "8px" }}>Lossless Headless Spooler</h3>
              <p style={{ fontSize: "14px", color: "var(--text-secondary)", lineHeight: 1.6 }}>
                Runs pytest, cargo, tsc, and git via <code>genesis run</code>. Captures raw outputs into local spools while feeding concise, line-anchored 80-token summaries to the LLM.
              </p>
            </div>

            {/* Bento Card 3 */}
            <div className="glass-panel" style={{ padding: "28px" }}>
              <div style={{ width: "40px", height: "40px", borderRadius: "10px", backgroundColor: "rgba(239, 68, 68, 0.15)", display: "flex", alignItems: "center", justifyContent: "center", marginBottom: "16px" }}>
                <Shield size={20} color="#EF4444" />
              </div>
              <h3 style={{ fontSize: "18px", fontWeight: 700, color: "#FFF", marginBottom: "8px" }}>Zero-Trust Secret Scrubber</h3>
              <p style={{ fontSize: "14px", color: "var(--text-secondary)", lineHeight: 1.6 }}>
                Air-gapped entropy and regex filters redact API keys (OpenAI, Anthropic, AWS, GitHub) before text is stored in SQLite or passed to telemetry.
              </p>
            </div>

            {/* Bento Card 4 */}
            <div className="glass-panel" style={{ padding: "28px" }}>
              <div style={{ width: "40px", height: "40px", borderRadius: "10px", backgroundColor: "rgba(129, 140, 248, 0.15)", display: "flex", alignItems: "center", justifyContent: "center", marginBottom: "16px" }}>
                <RefreshCw size={20} color="#818CF8" />
              </div>
              <h3 style={{ fontSize: "18px", fontWeight: 700, color: "#FFF", marginBottom: "8px" }}>Hebbian Sleep Cycles</h3>
              <p style={{ fontSize: "14px", color: "var(--text-secondary)", lineHeight: 1.6 }}>
                Simulates biological memory consolidation during idle times. Condenses ephemeral chat turns into permanent procedural skills and prunes stale artifacts.
              </p>
            </div>

            {/* Bento Card 5 */}
            <div className="glass-panel" style={{ padding: "28px" }}>
              <div style={{ width: "40px", height: "40px", borderRadius: "10px", backgroundColor: "rgba(245, 158, 11, 0.15)", display: "flex", alignItems: "center", justifyContent: "center", marginBottom: "16px" }}>
                <CheckCircle2 size={20} color="#F59E0B" />
              </div>
              <h3 style={{ fontSize: "18px", fontWeight: 700, color: "#FFF", marginBottom: "8px" }}>Rule Challenge Protocol</h3>
              <p style={{ fontSize: "14px", color: "var(--text-secondary)", lineHeight: 1.6 }}>
                Allows LLM agents to explicitly contest solidified rules via <code>challenge_rule()</code> with counter-evidence instead of silently hallucinating or breaking conventions.
              </p>
            </div>

            {/* Bento Card 6 */}
            <div className="glass-panel" style={{ padding: "28px" }}>
              <div style={{ width: "40px", height: "40px", borderRadius: "10px", backgroundColor: "rgba(168, 85, 247, 0.15)", display: "flex", alignItems: "center", justifyContent: "center", marginBottom: "16px" }}>
                <Database size={20} color="#A855F7" />
              </div>
              <h3 style={{ fontSize: "18px", fontWeight: 700, color: "#FFF", marginBottom: "8px" }}>Local-First SQLite WAL</h3>
              <p style={{ fontSize: "14px", color: "var(--text-secondary)", lineHeight: 1.6 }}>
                100% private and offline-capable. Stored under <code>~/.genesis/memory.db</code> with Write-Ahead Logging for sub-millisecond concurrent multi-client reads and writes.
              </p>
            </div>
          </div>
        </div>
      </section>

      {/* =====================================================================
          7. PRICING SECTION
          ===================================================================== */}
      <section id="pricing" style={{ padding: "80px 0", borderTop: "1px solid var(--border-subtle)" }}>
        <div className="container">
          <div style={{ textAlign: "center", maxWidth: "700px", margin: "0 auto 36px" }}>
            <div className="badge" style={{ marginBottom: "12px" }}>
              <Star size={14} color="#F59E0B" />
              <span>Transparent Developer Pricing</span>
            </div>
            <h2 style={{ fontSize: "36px", fontWeight: 800, letterSpacing: "-1px", marginBottom: "16px" }}>
              Start Free, Scale as Your Team Grows
            </h2>
            <p style={{ color: "var(--text-secondary)", fontSize: "16px", marginBottom: "24px" }}>
              GENESIS pays for itself within the first week through token reduction alone.
            </p>

            {/* Billing cycle toggle */}
            <div
              style={{
                display: "inline-flex",
                alignItems: "center",
                gap: "12px",
                background: "rgba(17, 24, 39, 0.6)",
                padding: "6px 14px",
                borderRadius: "var(--radius-full)",
                border: "1px solid var(--border-subtle)",
              }}
            >
              <span style={{ fontSize: "13px", color: !annualBilling ? "#FFF" : "var(--text-secondary)", fontWeight: !annualBilling ? 600 : 400 }}>
                Monthly
              </span>
              <button
                onClick={() => setAnnualBilling(!annualBilling)}
                style={{
                  width: "44px",
                  height: "22px",
                  borderRadius: "11px",
                  backgroundColor: annualBilling ? "#22C55E" : "#334155",
                  border: "none",
                  cursor: "pointer",
                  position: "relative",
                  transition: "background-color 200ms ease",
                }}
              >
                <div
                  style={{
                    width: "16px",
                    height: "16px",
                    borderRadius: "50%",
                    backgroundColor: "#FFF",
                    position: "absolute",
                    top: "3px",
                    left: annualBilling ? "25px" : "3px",
                    transition: "left 200ms ease",
                  }}
                ></div>
              </button>
              <span style={{ fontSize: "13px", color: annualBilling ? "#FFF" : "var(--text-secondary)", fontWeight: annualBilling ? 600 : 400 }}>
                Annual <span style={{ color: "#22C55E", fontSize: "11px", fontWeight: 700 }}>(Save 20%)</span>
              </span>
            </div>
          </div>

          <div
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(auto-fit, minmax(300px, 1fr))",
              gap: "24px",
              maxWidth: "1080px",
              margin: "0 auto",
            }}
          >
            {/* Community Tier */}
            <div className="glass-panel" style={{ padding: "32px", display: "flex", flexDirection: "column" }}>
              <div style={{ marginBottom: "20px" }}>
                <h3 style={{ fontSize: "20px", fontWeight: 700, color: "#FFF", marginBottom: "4px" }}>Community</h3>
                <p style={{ fontSize: "13px", color: "var(--text-secondary)" }}>For individual developers & hackers</p>
              </div>

              <div style={{ marginBottom: "24px" }}>
                <div className="font-mono" style={{ fontSize: "36px", fontWeight: 800, color: "#FFF" }}>
                  $0
                </div>
                <div style={{ fontSize: "12px", color: "var(--text-tertiary)" }}>Free forever · Open Source Core</div>
              </div>

              <ul style={{ listStyle: "none", display: "flex", flexDirection: "column", gap: "12px", marginBottom: "32px", flexGrow: 1 }}>
                <li style={{ display: "flex", alignItems: "center", gap: "10px", fontSize: "14px", color: "var(--text-secondary)" }}>
                  <Check size={16} color="#22C55E" /> 100% Local SQLite Memory DB
                </li>
                <li style={{ display: "flex", alignItems: "center", gap: "10px", fontSize: "14px", color: "var(--text-secondary)" }}>
                  <Check size={16} color="#22C55E" /> Standard Subconscious Hook
                </li>
                <li style={{ display: "flex", alignItems: "center", gap: "10px", fontSize: "14px", color: "var(--text-secondary)" }}>
                  <Check size={16} color="#22C55E" /> 1-Click Multi-Client Setup
                </li>
                <li style={{ display: "flex", alignItems: "center", gap: "10px", fontSize: "14px", color: "var(--text-secondary)" }}>
                  <Check size={16} color="#22C55E" /> Lossless Headless Spooler
                </li>
              </ul>

              <a href="#install" className="btn btn-secondary" style={{ width: "100%" }}>
                Install Open Source
              </a>
            </div>

            {/* Developer Pro Tier (Highlighted) */}
            <div
              className="glass-panel"
              style={{
                padding: "32px",
                display: "flex",
                flexDirection: "column",
                border: "2px solid #38BDF8",
                position: "relative",
                boxShadow: "0 0 35px rgba(56, 189, 248, 0.2)",
              }}
            >
              <div
                style={{
                  position: "absolute",
                  top: "-12px",
                  right: "24px",
                  backgroundColor: "#38BDF8",
                  color: "#07090E",
                  fontSize: "11px",
                  fontWeight: 700,
                  padding: "2px 10px",
                  borderRadius: "var(--radius-full)",
                  letterSpacing: "0.5px",
                }}
              >
                MOST POPULAR
              </div>

              <div style={{ marginBottom: "20px" }}>
                <h3 style={{ fontSize: "20px", fontWeight: 700, color: "#FFF", marginBottom: "4px" }}>Developer Pro</h3>
                <p style={{ fontSize: "13px", color: "var(--text-secondary)" }}>Power developers & daily AI coders</p>
              </div>

              <div style={{ marginBottom: "24px" }}>
                <div style={{ display: "flex", alignItems: "baseline", gap: "4px" }}>
                  <span className="font-mono" style={{ fontSize: "36px", fontWeight: 800, color: "var(--accent-cyan)" }}>
                    ${annualBilling ? "12" : "15"}
                  </span>
                  <span style={{ fontSize: "13px", color: "var(--text-tertiary)" }}>/ user / month</span>
                </div>
                <div style={{ fontSize: "12px", color: "var(--accent-emerald)" }}>Or $89 one-time lifetime license</div>
              </div>

              <ul style={{ listStyle: "none", display: "flex", flexDirection: "column", gap: "12px", marginBottom: "32px", flexGrow: 1 }}>
                <li style={{ display: "flex", alignItems: "center", gap: "10px", fontSize: "14px", color: "#FFF" }}>
                  <Check size={16} color="#38BDF8" /> Everything in Community, plus:
                </li>
                <li style={{ display: "flex", alignItems: "center", gap: "10px", fontSize: "14px", color: "#FFF" }}>
                  <Check size={16} color="#38BDF8" /> Advanced High-Ratio Compactor (65%+)
                </li>
                <li style={{ display: "flex", alignItems: "center", gap: "10px", fontSize: "14px", color: "#FFF" }}>
                  <Check size={16} color="#38BDF8" /> Hebbian Sleep Distillation Engine
                </li>
                <li style={{ display: "flex", alignItems: "center", gap: "10px", fontSize: "14px", color: "#FFF" }}>
                  <Check size={16} color="#38BDF8" /> Desktop Telemetry & Visual Orbit Deck
                </li>
                <li style={{ display: "flex", alignItems: "center", gap: "10px", fontSize: "14px", color: "#FFF" }}>
                  <Check size={16} color="#38BDF8" /> Priority Offline License Key
                </li>
              </ul>

              <button className="btn btn-primary" style={{ width: "100%" }}>
                Upgrade to Pro
              </button>
            </div>

            {/* Enterprise Gateway Tier */}
            <div className="glass-panel" style={{ padding: "32px", display: "flex", flexDirection: "column" }}>
              <div style={{ marginBottom: "20px" }}>
                <h3 style={{ fontSize: "20px", fontWeight: 700, color: "#FFF", marginBottom: "4px" }}>Enterprise Gateway</h3>
                <p style={{ fontSize: "13px", color: "var(--text-secondary)" }}>For engineering teams & organizations</p>
              </div>

              <div style={{ marginBottom: "24px" }}>
                <div style={{ display: "flex", alignItems: "baseline", gap: "4px" }}>
                  <span className="font-mono" style={{ fontSize: "36px", fontWeight: 800, color: "#FFF" }}>
                    ${annualBilling ? "32" : "39"}
                  </span>
                  <span style={{ fontSize: "13px", color: "var(--text-tertiary)" }}>/ dev / month</span>
                </div>
                <div style={{ fontSize: "12px", color: "var(--text-tertiary)" }}>Billed annually or custom invoice</div>
              </div>

              <ul style={{ listStyle: "none", display: "flex", flexDirection: "column", gap: "12px", marginBottom: "32px", flexGrow: 1 }}>
                <li style={{ display: "flex", alignItems: "center", gap: "10px", fontSize: "14px", color: "var(--text-secondary)" }}>
                  <Check size={16} color="#22C55E" /> Everything in Pro, plus:
                </li>
                <li style={{ display: "flex", alignItems: "center", gap: "10px", fontSize: "14px", color: "var(--text-secondary)" }}>
                  <Check size={16} color="#22C55E" /> Team-Wide Shared Memory & Onboarding
                </li>
                <li style={{ display: "flex", alignItems: "center", gap: "10px", fontSize: "14px", color: "var(--text-secondary)" }}>
                  <Check size={16} color="#22C55E" /> On-Prem Docker Reverse Proxy Gateway
                </li>
                <li style={{ display: "flex", alignItems: "center", gap: "10px", fontSize: "14px", color: "var(--text-secondary)" }}>
                  <Check size={16} color="#22C55E" /> Zero-Trust Security & Audit Compliance
                </li>
                <li style={{ display: "flex", alignItems: "center", gap: "10px", fontSize: "14px", color: "var(--text-secondary)" }}>
                  <Check size={16} color="#22C55E" /> Dedicated SLA & Implementation Support
                </li>
              </ul>

              <button className="btn btn-secondary" style={{ width: "100%" }}>
                Contact Enterprise Sales
              </button>
            </div>
          </div>
        </div>
      </section>

      {/* =====================================================================
          8. FAQ SECTION
          ===================================================================== */}
      <section style={{ padding: "80px 0", backgroundColor: "rgba(11, 15, 23, 0.4)" }}>
        <div className="container" style={{ maxWidth: "780px" }}>
          <div style={{ textAlign: "center", marginBottom: "40px" }}>
            <h2 style={{ fontSize: "32px", fontWeight: 800, letterSpacing: "-0.8px", marginBottom: "12px" }}>
              Frequently Asked Questions
            </h2>
            <p style={{ color: "var(--text-secondary)", fontSize: "15px" }}>
              Common questions from engineers deploying GENESIS Memory.
            </p>
          </div>

          <div style={{ display: "flex", flexDirection: "column", gap: "14px" }}>
            <div className="glass-panel" style={{ padding: "20px 24px" }}>
              <h4 style={{ fontSize: "16px", fontWeight: 600, color: "#FFF", marginBottom: "8px" }}>
                How is this different from Cursor rules or CLAUDE.md?
              </h4>
              <p style={{ fontSize: "14px", color: "var(--text-secondary)", lineHeight: 1.6 }}>
                Rules files are static and isolated to one IDE. GENESIS is a dynamic, living memory engine that shares working threads across Cursor, Claude Code, and OpenCode, automatically adapts over time, and enforces a strict token budget (&lt; 200 tokens) to prevent context degradation.
              </p>
            </div>

            <div className="glass-panel" style={{ padding: "20px 24px" }}>
              <h4 style={{ fontSize: "16px", fontWeight: 600, color: "#FFF", marginBottom: "8px" }}>
                Does my code or data get sent to external servers?
              </h4>
              <p style={{ fontSize: "14px", color: "var(--text-secondary)", lineHeight: 1.6 }}>
                No. GENESIS is local-first by design. All engrams, dialogue history, and spools live in your local SQLite database at <code>~/.genesis/memory.db</code>. With automated zero-trust secret scrubbing, credentials and private keys are redacted before anything touches disk.
              </p>
            </div>

            <div className="glass-panel" style={{ padding: "20px 24px" }}>
              <h4 style={{ fontSize: "16px", fontWeight: 600, color: "#FFF", marginBottom: "8px" }}>
                What happens if the proxy server goes down?
              </h4>
              <p style={{ fontSize: "14px", color: "var(--text-secondary)", lineHeight: 1.6 }}>
                GENESIS implements a hardened <strong>Transparent Fail-Open</strong> architecture. If any component encounters a syntax error or lock conflict, requests bypass directly to the upstream model provider without interrupting the developer's active coding workflow.
              </p>
            </div>
          </div>
        </div>
      </section>

      {/* =====================================================================
          9. FOOTER
          ===================================================================== */}
      <footer style={{ marginTop: "auto", borderTop: "1px solid var(--border-subtle)", padding: "48px 0 32px", backgroundColor: "#07090E" }}>
        <div className="container">
          <div
            style={{
              display: "flex",
              flexWrap: "wrap",
              alignItems: "center",
              justifyContent: "space-between",
              gap: "24px",
              marginBottom: "32px",
            }}
          >
            <div style={{ display: "flex", alignItems: "center", gap: "10px" }}>
              <div
                style={{
                  width: "28px",
                  height: "28px",
                  borderRadius: "8px",
                  background: "linear-gradient(135deg, #38BDF8 0%, #2563EB 100%)",
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                }}
              >
                <Cpu size={16} color="#07090E" />
              </div>
              <span style={{ fontWeight: 800, fontSize: "16px" }}>
                GENESIS<span style={{ color: "var(--accent-cyan)" }}>.memory</span>
              </span>
            </div>

            <div style={{ display: "flex", gap: "24px", fontSize: "14px", color: "var(--text-secondary)" }}>
              <a href="#features" style={{ color: "inherit", textDecoration: "none" }}>Features</a>
              <a href="#calculator" style={{ color: "inherit", textDecoration: "none" }}>ROI Calculator</a>
              <a href="#pricing" style={{ color: "inherit", textDecoration: "none" }}>Pricing</a>
              <a href="https://github.com/HamidRezaeian/genesis-memory" target="_blank" rel="noreferrer" style={{ color: "inherit", textDecoration: "none" }}>GitHub</a>
            </div>
          </div>

          <div style={{ display: "flex", flexWrap: "wrap", justifyContent: "space-between", fontSize: "12px", color: "var(--text-tertiary)" }}>
            <p>© 2026 GENESIS Memory. MIT Core License. Engineered for autonomous AI pairing.</p>
            <p>Designed with /ui-ux-pro-max and Framer Motion.</p>
          </div>
        </div>
      </footer>
    </div>
  );
}
