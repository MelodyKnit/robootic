# 📚 Documentation Index

This directory contains all project documentation organized by category.

## 🚀 Getting Started

- **[Quick Start Guide](quick-start.md)** - Get up and running with pick-and-place operations
- **[Project Roadmap](roadmap.md)** - Development milestones and future plans

## 📖 User Guides

- **[Detection & Pose Setup](guides/detection-pose-setup.md)** - Configure object detection and pose estimation
- **[Vision System Improvement](guides/vision-improvement.md)** - Enhance vision subsystem performance

## 🔌 API Documentation

- **[Architecture Overview](architecture.md)** - System architecture and design patterns
- **[Object Detection API](object-detection-api.md)** - Object detection service endpoints
- **[Object Pose API](object-pose-api.md)** - Pose estimation service endpoints
- **[Calibration API](object-pose-calibration.md)** - Camera and hand-eye calibration
- **[Plugin Camera Binding](plugin-camera-binding.md)** - Camera plugin integration
- **[Recording Feature Plan](recording-feature-plan.md)** - Recording system design

## 🎨 Design Documents

- **[Automatic Calibration](design/auto-calibration.md)** - Automated intrinsic calibration design
- **[Web Calibration Plugin](design/web-calibration-plugin.md)** - Browser-based calibration UI
- **[Hardware Control Enhancement](design/hardware-control-enhancement.md)** - Low-level hardware improvements
- **[Recording Integration](design/recording-integration.md)** - Recording system integration
- **[Vision Enhancement Plan](design/vision-enhancement-plan.md)** - Vision subsystem roadmap

## 📊 Implementation Reports

- **[Auto Calibration Implementation](reports/auto-calibration-implementation.md)** - CLI calibration system completion
- **[Web Calibration Implementation](reports/web-calibration-implementation.md)** - Web plugin system completion

---

## 📂 Documentation Structure

```
docs/
├── README.md                           # This file - documentation index
├── quick-start.md                      # Quick start guide
├── roadmap.md                          # Project roadmap
│
├── api/                                # (Future) Detailed API docs
├── guides/                             # User and developer guides
│   ├── detection-pose-setup.md
│   └── vision-improvement.md
│
├── design/                             # Design documents and plans
│   ├── auto-calibration.md
│   ├── web-calibration-plugin.md
│   ├── hardware-control-enhancement.md
│   ├── recording-integration.md
│   └── vision-enhancement-plan.md
│
└── reports/                            # Implementation reports
    ├── auto-calibration-implementation.md
    └── web-calibration-implementation.md
```

## 🔍 Finding Documentation

- **For users**: Start with [Quick Start Guide](quick-start.md)
- **For developers**: Review [Architecture](architecture.md) first
- **For vision work**: See [Vision Enhancement Plan](design/vision-enhancement-plan.md)
- **For calibration**: Check [Auto Calibration Design](design/auto-calibration.md)

## 📝 Documentation Guidelines

When adding new documentation:

1. **User guides** → `docs/guides/` - How-to and setup instructions
2. **API docs** → `docs/api/` - Endpoint specifications and schemas
3. **Design docs** → `docs/design/` - Architecture decisions and plans
4. **Reports** → `docs/reports/` - Implementation summaries and milestones

Keep session notes and temporary documents in `localstore/session-notes/` (not tracked by git).
