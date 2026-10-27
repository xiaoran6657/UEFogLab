# Primary references / 主参考索引

Only links and original summaries are included. Third-party originals retain their own terms and are not distributed. Sources inform the model; they do not establish implemented features or owner study.

- B01: [PBRT 4e, 11.1 Volume Scattering Processes](https://pbr-book.org/4ed/Volume_Scattering/Volume_Scattering_Processes) — 吸收、出散射、入散射与消光系数的定义。 概念依据；不代表已实现完整输运。
- B02: [PBRT 4e, 11.2 Transmittance](https://pbr-book.org/4ed/Volume_Scattering/Transmittance) — 光学厚度、Beer 定律与分段透射率的乘法性质。 M1 优先读；原始 S/T 验证不能用色调映射截图替代。
- B03: [PBRT 4e, 11.3 Phase Functions](https://pbr-book.org/4ed/Volume_Scattering/Phase_Functions) — HG、归一化和方向约定。 PBRT 两方向均离开散射点；不能直接套用另一方向约定的符号。
- B04: [PBRT 4e, 14.1 The Equation of Transfer](https://pbr-book.org/4ed/Light_Transport_II_Volume_Rendering/The_Equation_of_Transfer) — 体积输运方程与沿视线积分的关系。 首版只取必要的确定性积分思路，不扩展为完整蒙特卡洛渲染器。
- R01: [Joint Bilateral Upsampling](https://johanneskopf.de/publications/jbu/) — 利用高分辨率引导信号重建低分辨率解。 当前四邻域深度加权是受其启发的简化，不是完整论文复现。
- R02: [Bilateral Filtering for Gray and Color Images](https://users.soe.ucsc.edu/~manduchi/Papers/ICCV98.pdf) — 空间距离与信号相似度的联合权重、归一化。 双边滤波基础；深度引导与低分辨率积分仍需单独设计和验证。
- R03: [GPU Gems 3, Chapter 23: High-Speed, Off-Screen Particles](https://developer.nvidia.com/gpugems/gpugems3/part-iv-image-effects/chapter-23-high-speed-screen-particles) — 低分辨率特效、深度降采样和合成边缘问题。 粒子方法而非局部雾射线积分；旧 DirectX 细节不能照搬 UE5。
- V01: [Volumetric Fog: Unified, Compute Shader Based Solution to Atmospheric Scattering](https://bartwronski.com/publications/) — 视锥体积表示、密度／光照／积分分工与实时近似。 演讲讲义含备注，不是期刊论文；首版不实现整套 froxel 系统。
- V02: [Physically-based and Unified Volumetric Rendering in Frostbite](https://advances.realtimerendering.com/s2015/index.html) — 统一介质表示、分段积分、光照与阴影近似。 会议主页题名为 Towards Unified and Physically-Based Volumetric Lighting in Frostbite；讲义不是论文，含视频体积较大。作者旧 EA 入口现为 404。
- V03: [Fast, Flexible, Physically-Based Volumetric Light Scattering (presenter notes)](https://developer.download.nvidia.com/gameworks/events/GDC2016/Fast%20Flexible%20Physically-Based%20Volumetric%20Light%20Scattering%20-%20Notes.pdf) — 单次散射、光源路径与观察路径、可见性对积分的影响。 采用不同的可见性／积分方案；只作拓展，不要求引入 NVIDIA 运行库。
- E01: [Unreal Engine: Render Dependency Graph](https://dev.epicgames.com/documentation/en-us/unreal-engine/render-dependency-graph-in-unreal-engine) — RDG pass、参数声明、资源生命周期与验证。 API 最终以本地 UE 5.8.2 源码签名为准，不复制引擎源码进仓库。
- E02: [Unreal Engine: Post Process Materials](https://dev.epicgames.com/documentation/en-us/unreal-engine/post-process-materials-in-unreal-engine) — HDR、场景输入、后处理位置与视图坐标。 帮助理解合成；当前实现是 C++ 全局 shader／RDG 插件，非该页的材质方案。
- E03: [Unreal Engine: Local Fog Volumes](https://dev.epicgames.com/documentation/en-us/unreal-engine/local-fog-volumes-in-unreal-engine) — 原生径向／高度密度、合成顺序及与 VF 组合。 独立 LFV 与 LFV 注入 VF 的能力和成本不同；不能假设与自研径向密度完全匹配。
- E04: [Unreal Engine: Volumetric Fog](https://dev.epicgames.com/documentation/en-us/unreal-engine/volumetric-fog-in-unreal-engine) — 网格、局部介质、灯光、阴影与历史滤波。 原生特性对照；文档历史硬件的耗时不能引用为本机实测。
- E05: [Unreal Engine: Path Tracer](https://dev.epicgames.com/documentation/en-us/unreal-engine/path-tracer-in-unreal-engine) — 离线路径追踪的设置、受支持表示和参考图边界。 不能直接追踪自研后处理中的密度；未匹配模型前仅作物理视觉参考。
- E06: [Unreal Engine: Heterogeneous Volumes](https://dev.epicgames.com/documentation/en-us/unreal-engine/heterogeneous-volumes-in-unreal-engine) — SVT／VDB 与原生异质介质、Path Tracer 的表示。 可选桥接路线；首周不因资料存在就开发额外 VDB 管线。
- E07: [Unreal Engine: Exponential Height Fog](https://dev.epicgames.com/documentation/en-us/unreal-engine/exponential-height-fog-in-unreal-engine) — VF 的 EHF 容器、背景高度雾与独立控制项。 页面部分历史描述须结合 E04 和本地源码；不能靠零密度／开关请求推断 native VF 已运行。
- A01: [Unreal Engine: Mobile Rendering and Shading Modes](https://dev.epicgames.com/documentation/en-us/unreal-engine/mobile-rendering-and-shading-modes-for-unreal-engine) — 移动前向、延迟和桌面渲染器的区别。 文档说明不能代替实际设备／路径验证。
- A02: [Unreal Engine: Android Development Requirements](https://dev.epicgames.com/documentation/en-us/unreal-engine/android-development-requirements-for-unreal-engine) — 指定 UE 版本的 SDK／NDK／JDK 与设备要求。 此页会更新；安装前重新核对本地引擎与实时官方要求。
- P01: [Unreal Engine: Unreal Insights Trace Quick Start Guide](https://dev.epicgames.com/documentation/en-us/unreal-engine/trace-quick-start-guide-in-unreal-engine) — 追踪流程、GPU／CPU 通道与会话记录。 取证抓帧、浮点读回与正式性能运行分开。
- P02: [Unreal Engine Performance Guide](https://gpuopen.com/learn/unreal-engine-performance-guide/) — 测量噪声、构建配置、GPU 分析与可重复实验。 方法参考；不把其他 GPU 或旧 UE 的优化结论直接套用 RX 9070。
- D01: [RenderDoc: Quick Start](https://renderdoc.org/docs/getting_started/quick_start.html) — 图形抓帧与资源检查的操作入口。 用于调试取证；抓帧后的 GPU 计时不直接作为正式性能数据。
