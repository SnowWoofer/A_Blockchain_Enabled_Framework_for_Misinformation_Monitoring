import logging

import torch
from transformers import AutoModelForSequenceClassification, AutoTokenizer

from .config import settings

logger = logging.getLogger(__name__)


class MisinfoModel:
    """Loads the fine-tuned AfroXLM-R checkpoint and applies the requested
    device / quantization combination.

    Quantization support is backend-dependent:
      - cuda: int8 / 4bit via bitsandbytes (BitsAndBytesConfig), fp16 via
        torch_dtype, none = full fp32 precision (max-throughput server test).
      - cpu:  int8 via torch's built-in dynamic quantization (CPU-only kernel,
        no CUDA needed) to shrink AfroXLM-R Large towards ~700MB. 4bit has no
        CPU kernel in stock PyTorch, so it falls back to int8 with a warning.
      - mps:  bitsandbytes and torch dynamic quantization both lack MPS
        kernels, so int8/4bit requests fall back to unquantized fp32/fp16 on
        mps with a warning rather than silently moving the model to cpu.
    """

    def __init__(self):
        self.device = self._resolve_device(settings.torch_device)
        self.tokenizer = AutoTokenizer.from_pretrained(settings.model_path)
        self.model = self._load_model()
        self.model.eval()
        self.id2label = self.model.config.id2label

    @staticmethod
    def _resolve_device(requested: str) -> torch.device:
        requested = requested.lower()
        if requested == "cuda" and not torch.cuda.is_available():
            logger.warning("TORCH_DEVICE=cuda requested but CUDA is not available; falling back to cpu")
            return torch.device("cpu")
        if requested == "mps" and not torch.backends.mps.is_available():
            logger.warning("TORCH_DEVICE=mps requested but MPS is not available; falling back to cpu")
            return torch.device("cpu")
        return torch.device(requested)

    def _load_model(self):
        quant = settings.model_quantization.lower()
        device_type = self.device.type

        if quant in ("int8", "4bit") and device_type == "cuda":
            from transformers import BitsAndBytesConfig

            bnb_config = BitsAndBytesConfig(
                load_in_8bit=quant == "int8",
                load_in_4bit=quant == "4bit",
                bnb_4bit_compute_dtype=torch.float16,
            )
            logger.info("Loading model with bitsandbytes %s quantization on cuda", quant)
            return AutoModelForSequenceClassification.from_pretrained(
                settings.model_path, quantization_config=bnb_config, device_map={"": 0}
            )

        if quant == "4bit" and device_type != "cuda":
            logger.warning(
                "4bit quantization requires CUDA (bitsandbytes); no fallback kernel exists "
                "on %s. %s",
                device_type,
                "Falling back to int8 dynamic quantization." if device_type == "cpu" else "Running unquantized.",
            )
            quant = "int8" if device_type == "cpu" else "none"

        if quant == "int8" and device_type != "cpu":
            logger.warning(
                "int8 dynamic quantization only has a CPU kernel in stock PyTorch; "
                "running unquantized on %s instead",
                device_type,
            )
            quant = "none"

        if quant == "int8" and device_type == "cpu":
            logger.info("Loading fp32 model then applying dynamic int8 quantization on cpu")
            model = AutoModelForSequenceClassification.from_pretrained(settings.model_path)
            return torch.quantization.quantize_dynamic(model, {torch.nn.Linear}, dtype=torch.qint8)

        torch_dtype = torch.float16 if quant == "fp16" else None
        logger.info("Loading model dtype=%s on device=%s", torch_dtype or "fp32", self.device)
        model = AutoModelForSequenceClassification.from_pretrained(
            settings.model_path, torch_dtype=torch_dtype
        )
        model.to(self.device)
        return model

    @torch.no_grad()
    def predict_batch(self, texts: list[str]) -> list[dict]:
        encoded = self.tokenizer(
            texts,
            padding=True,
            truncation=True,
            max_length=settings.model_max_length,
            return_tensors="pt",
        )
        encoded = {k: v.to(self.device) for k, v in encoded.items()}
        logits = self.model(**encoded).logits
        probs = torch.softmax(logits.float(), dim=-1).cpu().numpy()

        results = []
        for row in probs:
            misinfo_prob = float(row[1])
            # The model's own prediction (argmax), not a tunable policy cut.
            # label_binary is the ledger's domain ("0"/"1", enforced by the
            # chaincode) and is emitted here rather than re-derived downstream:
            # the string in `label` comes from this model's id2label and is not
            # stable across models, so only this service can map it safely.
            predicted = 1 if misinfo_prob >= 0.5 else 0
            results.append(
                {
                    "label": self.id2label[predicted],
                    "label_binary": str(predicted),
                    # Probability of the predicted class, so always >= 0.5.
                    "confidence": float(row[predicted]),
                    "misinformation_probability": misinfo_prob,
                }
            )
        return results
