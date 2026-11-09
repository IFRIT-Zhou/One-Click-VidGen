# BiRefNet architecture and config

This directory includes the architecture and configuration used with
[BiRefNet](https://github.com/ZhengPeng7/BiRefNet) by Peng Zheng and collaborators.
The source snapshot is [1038lab/BiRefNet, revision
4d000788a9698c7f8d67c8c6ce2b40c768f5b909](https://huggingface.co/1038lab/BiRefNet/tree/4d000788a9698c7f8d67c8c6ce2b40c768f5b909).
The [original author's license](https://github.com/ZhengPeng7/BiRefNet/blob/main/LICENSE)
and the [Swin Transformer license](https://github.com/microsoft/Swin-Transformer/blob/main/LICENSE)
are MIT; notices are reproduced in LICENSE. Original code comments remain intact.

The two Python sources match the pinned snapshot. config.json differs only by
a trailing newline. At load time the offline OCV worker resolves the config
import locally and adapts a Swin attention-mask cast for half precision.
No weights, ComfyUI nodes or user media are bundled here.
