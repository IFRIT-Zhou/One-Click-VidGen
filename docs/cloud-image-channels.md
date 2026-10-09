# Cloud image channels

Cloud image requests can select `method: "running"` (the default) or
`method: "ican"`. This setting affects image generation only. Text models,
speech synthesis and video generation keep their existing routing.

The shared frame/quality control offers RunningHub 1K, 2K and 4K. For ICAN
GPT Image 2.5, landscape and portrait video frames offer 2K and 2.5K, defaulting
to 2.5K. The desktop converts this choice to `size`:

| Frame | Quality | Size |
| --- | --- | --- |
| 16:9 | 2K / 2.5K | 2048x1152 / 2560x1440 |
| 9:16 | 2K / 2.5K | 1152x2048 / 1440x2560 |
| 1:1 (image studio) | 1K | 1024x1024 |
| 3:2 (image studio) | 1K | 1536x1024 |
| 2:3 (image studio) | 1K | 1024x1536 |

These are the seven regular sizes enabled in the gateway; experimental sizes
are not offered. Omitting size defaults to 2560x1440. RunningHub continues to
use `aspectRatio` and `resolution`; ICAN uses `model: "gpt-image-2.5"` and
`size` instead. Requests include the matching `provider` for compatibility.

Gateway deployment must support ICAN generation, pricing for these sizes,
channel-specific reference upload and authenticated result downloads. ICAN
references use `/image-pool/media/upload?provider=ican`; failed uploads stop
submission rather than falling back to unsupported data URIs. Upstream API
keys stay on the gateway. Users do not enter task IDs; internal IDs deduplicate
submission retries.

Selections are retained in task parameters, including existing-project edits
and redraws. Switching channels does not replace already generated images.

ICAN retail pricing rounds the upstream cost in CNY up to 0.01 per image,
without an additional percentage markup. The current 0.035 CNY cost becomes
0.04 credits per image for all seven regular sizes. Existing jobs retain
their saved price; video jobs reserve the sum of per-image rounded prices.
