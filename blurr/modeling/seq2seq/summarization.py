# AUTOGENERATED! DO NOT EDIT! File to edit: nbs/11_modeling-seq2seq-summarization.ipynb (unless otherwise specified).

__all__ = ['BlearnerForSummarization']

# Cell
import inspect, torch
from typing import Callable, Dict, List, Optional, Union

from fastai.callback.all import *
from fastai.data.block import DataBlock, ColReader, ItemGetter, ColSplitter, RandomSplitter
from fastai.data.core import DataLoaders
from fastai.imports import *
from fastai.learner import *
from fastai.torch_core import *
from fastai.torch_imports import *
from fastcore.all import *
from transformers import AutoModelForSeq2SeqLM, PreTrainedModel, logging

from ...utils import BLURR
from ...data.seq2seq.core import Seq2SeqBatchTokenizeTransform, Seq2SeqTextBlock
from ..core import BaseModelCallback, BaseModelWrapper, Blearner, PreCalculatedCrossEntropyLoss
from .core import Seq2SeqMetricsCallback, blurr_seq2seq_splitter

logging.set_verbosity_error()


# Cell
@patch
def blurr_summarize(self: Learner, inp, **kwargs):
    preds = learn.blurr_generate(inp, **kwargs)
    return [{"summary_text": pred} for pred in preds]

# Cell
@delegates(Blearner.__init__)
class BlearnerForSummarization(Blearner):
    def __init__(self, dls: DataLoaders, hf_model: PreTrainedModel, **kwargs):
        super().__init__(dls, hf_model, **kwargs)

    @classmethod
    def get_model_cls(cls):
        return AutoModelForSeq2SeqLM

    @classmethod
    def _add_t5_prefix(cls, inp):
        return f"summarize: {inp}"

    @classmethod
    def get_metrics_cb(self):
        seq2seq_metrics = {
            "rouge": {
                "compute_kwargs": {"rouge_types": ["rouge1", "rouge2", "rougeL", "rougeLsum"], "use_stemmer": True},
                "returns": ["rouge1", "rouge2", "rougeL", "rougeLsum"],
            },
            "bertscore": {"compute_kwargs": {"lang": "en"}, "returns": ["precision", "recall", "f1"]},
        }

        return Seq2SeqMetricsCallback(custom_metrics=seq2seq_metrics)

    @classmethod
    def from_data(
        cls,
        # Your raw dataset. Supports DataFrames, Hugging Face Datasets, as well as file paths
        # to .csv, .xlsx, .xls, and .jsonl files
        data: Union[pd.DataFrame, Path, str, List[Dict]],
        # The name or path of the pretrained model you want to fine-tune
        pretrained_model_name_or_path: Optional[Union[str, os.PathLike]],
        # The attribute in your dataset that contains your raw text
        text_attr: str = "text",
        # The attribute in your dataset that contains your target (summarized) text
        summary_attr: str = "summary",
        # The max length of your raw text to consider for summarization
        max_length: Union[int, str] = None,
        # The max length of your targets (sumamrized) text
        max_target_length: Union[int, str] = None,
        # A function that will split your Dataset into a training and validation set
        # See [here](https://docs.fast.ai/data.transforms.html#Split) for a list of fast.ai splitters
        dblock_splitter: Optional[Callable] = None,
        # Any additional keyword arguments applied during tokenization
        hf_tok_kwargs: dict = {},
        # If you want to override your Blurr transform's `text_gen_kwargs`, do that here
        text_gen_kwargs: dict = {},
        # Any kwargs to pass to your `DataLoaders`
        dl_kwargs: dict = {},
        # Any kwargs to pass to your task specific `Blearner`
        learner_kwargs: dict = {},
    ):
        # if we get a path/str then we're loading something like a .csv file
        if isinstance(data, Path) or isinstance(data, str):
            content_type = mimetypes.guess_type(data)[0]
            if content_type  == 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet':
                data = pd.read_excel(data)
            elif content_type  == 'text/csv':
                data = pd.read_csv(data)
            elif content_type  == 'application/json':
                data = pd.read_json(data, orient='records')
            else:
                raise ValueError("'data' must be a .xlsx, .xls, .csv, or .jsonl file")

            data = pd.read_csv(data)

        # infer our datablock splitter if None
        if dblock_splitter is None:
            dblock_splitter = ColSplitter() if hasattr(data, "is_valid") else RandomSplitter()

        # we need to find the architecture to ensure "mbart" specific tokenizer kwargs are included
        model_cls = cls.get_model_cls()
        model = model_cls.from_pretrained(pretrained_model_name_or_path)
        hf_arch = BLURR.get_model_architecture(type(model).__name__)

        if hf_arch == "mbart":
            hf_tok_kwargs = {**{"src_lang": "en_XX", "tgt_lang": "en_XX"}, **hf_tok_kwargs}

        # get our hf objects
        hf_arch, hf_config, hf_tokenizer, hf_model = BLURR.get_hf_objects(
            pretrained_model_name_or_path, model_cls=model_cls, tokenizer_kwargs=hf_tok_kwargs
        )

        # update text generation kwargs
        if text_gen_kwargs is None and hf_arch in ["bart", "t5"]:
            text_gen_kwargs = hf_config.task_specific_params["summarization"]

        # not all "summarization" parameters are for the model.generate method ... remove them here
        generate_func_args = list(inspect.signature(hf_model.generate).parameters.keys())
        for k in text_gen_kwargs.copy():
            if k not in generate_func_args:
                del text_gen_kwargs[k]

        # update our text generation kwargs for mbart
        if hf_arch == "mbart":
            text_gen_kwargs = {**{"decoder_start_token_id": "en_XX"}, **text_gen_kwargs}

        # define getters
        get_x = Pipeline(funcs=[ItemGetter(text_attr)])
        get_y = ItemGetter(summary_attr)

        if hf_arch == "t5":
            get_x.add(cls._add_t5_prefix)

        # define our DataBlock and DataLoaders
        batch_tokenize_tfm = Seq2SeqBatchTokenizeTransform(
            hf_arch,
            hf_config,
            hf_tokenizer,
            hf_model,
            max_length=max_length,
            max_target_length=max_target_length,
            text_gen_kwargs=text_gen_kwargs,
        )

        blocks = (Seq2SeqTextBlock(batch_tokenize_tfm=batch_tokenize_tfm), noop)
        dblock = DataBlock(blocks=blocks, get_x=get_x, get_y=get_y, splitter=dblock_splitter)

        dls = dblock.dataloaders(data, **dl_kwargs.copy())

        # return BLearner instance
        learner_kwargs["splitter"] = learner_kwargs.pop("splitter", partial(blurr_seq2seq_splitter, arch=hf_arch))
        learner_kwargs["loss_func"] = learner_kwargs.pop("loss_func", PreCalculatedCrossEntropyLoss())

        return cls(dls, hf_model, **learner_kwargs.copy())