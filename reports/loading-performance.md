# Loading performance

Measured September 19, 2026 on the local Windows machine, using headless Microsoft Edge at 1500 × 1000. Both runs used the same demonstration sites and normal camera animations. Each site opened in a fresh browser context. These are individual local measurements, not averages or network service guarantees; provider caches and hardware affect timings.

| Measurement | Before | After |
| --- | ---: | ---: |
| Solar: navigation to candidate map pins | 708 ms | 389 ms |
| Solar: 3D button to terrain-ready status | 6,094 ms | 1,795 ms |
| Solar: longest main-thread task | 6,708 ms | 176 ms |
| Wind: navigation to candidate map pins | 1,083 ms | 830 ms |
| Wind: 3D button to terrain-ready status | 2,478 ms | 2,441 ms |
| Wind: longest main-thread task | 2,683 ms | 274 ms |
| Local frontend asset transfer through 3D opening | 4,893,458 bytes | 576,567 bytes |
| Unused dark-basemap requests in the solar run | 60 | 0 |

The before run used the Vite development server; the after run used the optimized build through Vite preview at the same port, with the same API server. Transfer figures exclude API responses and external map imagery. External imagery response sizes are not exposed by browser resource timing. Terrain readiness now also checks source loading; previously, the status could report readiness while MapLibre returned temporary zero elevations.

The CPU profile identified repeated terrain tile coverage calculations as the main solar bottleneck. Batching coverage and caching repeated coordinates removed that work without approximating elevations or reducing panel, turbine, terrain, or shadow detail. Solar 3D readiness improved about 71%; local frontend asset transfer fell about 88%. Wind's terrain-ready timing was roughly unchanged, while its longest UI stall fell substantially.

Validation: 31 frontend tests pass. A real-tile browser check compared 300 samples across zoom/pitch/bearing settings and found zero elevation difference versus MapLibre's normal API. Browser checks verified a shared map canvas, terrain, spacing-linked capacity and costs, financial edits, mobile resize, and closing/reopening a project without page errors. Backend analysis and ML training were unchanged.

`npm start` now builds and serves the optimized frontend. `npm run start:dev` retains hot reload. Existing optimized preview sessions serve a new `dist` after `npm run build`; refresh the browser after rebuilding.
