import os
import sys

import torch

from transformers import AutoTokenizer, GenerationConfig, AutoModelForCausalLM, AutoConfig, BitsAndBytesConfig
from peft import PeftConfig, PeftModel


def load_saiga(
    model_name: str,
    use_4bit: bool = False,
    torch_compile: bool = False,
    torch_dtype: str = None,
    is_lora: bool = True,
    use_flash_attention_2: bool = True
):
    device = "cuda" if torch.cuda.is_available() else "cpu"
    try:
        tokenizer = AutoTokenizer.from_pretrained(model_name, use_fast=True)
    except:
        tokenizer = AutoTokenizer.from_pretrained(model_name, use_fast=False)

    print("PAD: ", tokenizer.pad_token_id, tokenizer.pad_token)
    print("BOS: ", tokenizer.bos_token_id, tokenizer.bos_token)
    print("EOS: ", tokenizer.eos_token_id, tokenizer.eos_token)
    print("UNK: ", tokenizer.unk_token_id, tokenizer.unk_token)
    print("SEP: ", tokenizer.sep_token_id, tokenizer.sep_token)

    tokenizer.padding_side = "left"
    tokenizer.pad_token = "<unk>"
    tokenizer.pad_token_id = 0
    tokenizer.add_bos_token = False
    tokenizer.add_eos_token = False

    print("PAD: ", tokenizer.pad_token_id, tokenizer.pad_token)
    print("BOS: ", tokenizer.bos_token_id, tokenizer.bos_token)
    print("EOS: ", tokenizer.eos_token_id, tokenizer.eos_token)
    print("UNK: ", tokenizer.unk_token_id, tokenizer.unk_token)
    print("SEP: ", tokenizer.sep_token_id, tokenizer.sep_token)
    
    generation_config = GenerationConfig.from_pretrained(model_name)

    if not is_lora:
        model = AutoModelForCausalLM.from_pretrained(
            model_name,
            load_in_8bit=True,
            device_map="auto",
            use_flash_attention_2=use_flash_attention_2
        )
        model.eval()
        return model, tokenizer, generation_config

    config = PeftConfig.from_pretrained(model_name)
    base_model_config = AutoConfig.from_pretrained(config.base_model_name_or_path)

    if torch_dtype is not None:
        torch_dtype = getattr(torch, torch_dtype)
    else:
        torch_dtype = base_model_config.torch_dtype

    if device == "cuda":
        if use_4bit:
            model = AutoModelForCausalLM.from_pretrained(
                config.base_model_name_or_path,
                torch_dtype=torch_dtype,
                load_in_4bit=True,
                device_map="auto",
                quantization_config=BitsAndBytesConfig(
                    load_in_4bit=True,
                    llm_int8_threshold=6.0,
                    llm_int8_has_fp16_weight=False,
                    bnb_4bit_compute_dtype=torch_dtype,
                    bnb_4bit_use_double_quant=True,
                    bnb_4bit_quant_type="nf4"
                ),
                use_flash_attention_2=use_flash_attention_2
            )
        else:
            model = AutoModelForCausalLM.from_pretrained(
                config.base_model_name_or_path,
                torch_dtype=torch_dtype,
                load_in_8bit=False,
                device_map="auto",
                use_flash_attention_2=use_flash_attention_2
            )
        model = PeftModel.from_pretrained(
            model,
            model_name,
            torch_dtype=torch_dtype
        )
    elif device == "cpu":
        model = AutoModelForCausalLM.from_pretrained(
            config.base_model_name_or_path,
            device_map={"": device},
            low_cpu_mem_usage=True
        )
        model = PeftModel.from_pretrained(
            model,
            model_name,
            device_map={"": device}
        )

    model.eval()
    if torch_compile and torch.__version__ >= "2" and sys.platform != "win32":
        model = torch.compile(model)
    return model, tokenizer, generation_config
