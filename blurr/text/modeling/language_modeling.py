# AUTOGENERATED! DO NOT EDIT! File to edit: nbs/12_text-modeling-language-modeling.ipynb (unless otherwise specified).

__all__ = ['LMMetricsCallback', 'BlearnerForLM']

# Cell
import ast, gc, inspect, os
from typing import Any, Callable, Dict, List, Optional, Union, Type

from fastcore.all import *
from fastai.callback.all import *
from fastai.data.block import DataBlock, ColReader, ItemGetter, ColSplitter, RandomSplitter
from fastai.data.core import DataLoader, DataLoaders, TfmdDL
from fastai.imports import *
from fastai.learner import *
from fastai.losses import CrossEntropyLossFlat
from fastai.optimizer import Adam, OptimWrapper, params
from fastai.metrics import perplexity
from fastai.torch_core import *
from fastai.torch_imports import *
from fastprogress.fastprogress import progress_bar, master_bar
from sklearn.metrics import accuracy_score
from transformers import AutoModelForCausalLM, AutoModelForMaskedLM, logging, PretrainedConfig, PreTrainedTokenizerBase, PreTrainedModel

from ..data.core import TextDataLoader, TextBlock, first_blurr_tfm
from ..data.language_modeling import (
    BaseLMStrategy,
    LMBatchTokenizeTransform,
    LMPreprocessor,
    LMType,
    CausalLMTextInput,
    CausalLMStrategy,
    MLMTextInput,
    BertMLMStrategy,
)
from .core import Blearner
from ..utils import NLP
from ...utils import PreCalculatedCrossEntropyLoss

logging.set_verbosity_error()


# Cell
class LMMetricsCallback(Callback):
    """A fastai friendly metric implemented as a callback so that we can handle use cases where we don't
    want to count tokens marked to be ignored or else not count batches where there are no targs
    """

    def __init__(self, **kwargs):
        self.run_before = Recorder

        self.custom_metrics_dict = {"lm_accuracy": None}
        self.do_setup = True

    def setup(self):
        # one time setup code here.
        if not self.do_setup:
            return

        # add custom text generation specific metrics
        custom_metric_keys = self.custom_metrics_dict.keys()
        custom_metrics = L([ValueMetric(partial(self.metric_value, metric_key=k), k) for k in custom_metric_keys])
        self.learn.metrics = self.learn.metrics + custom_metrics

        self.do_setup = False

    def before_fit(self):
        self.setup()

    # --- batch begin/after phases ---
    def after_batch(self):
        # do this only for validation set
        if self.training or self.learn.y is None:
            return

        preds = self.pred.argmax(dim=-1)
        targs = self.yb[0]  # yb is TensorText tuple, item 0 is the data

        msk = torch.where(targs != -100, 1, 0).bool()
        preds = torch.masked_select(preds, msk).cpu()
        targs = torch.masked_select(targs, msk).cpu()

        if preds.shape[0] == 0:
            return

        self.results += [(res[0], res[1]) for res in zip(preds, targs)]

    # --- validation begin/after phases ---
    def before_validate(self):
        self.results = []

    def after_validate(self):
        if len(self.results) < 1:
            return

        preds, targs = map(list, zip(*self.results))
        self.custom_metrics_dict["lm_accuracy"] = accuracy_score(targs, preds)

    # --- for ValueMetric metrics ---
    def metric_value(self, metric_key):
        return self.custom_metrics_dict[metric_key]


# Cell
@typedispatch
def show_results(
    # This typedispatched `show_results` will be called for `HF_CausalLMInput` typed inputs
    x: CausalLMTextInput,
    # Your targets
    y,
    # Your raw inputs/targets
    samples,
    # The model's predictions
    outs,
    # Your `Learner`. This is required so as to get at the Hugging Face objects for decoding them into
    # something understandable
    learner,
    # Your `show_results` context
    ctxs=None,
    # The maximum number of items to show
    max_n=6,
    # Any truncation your want applied to your decoded inputs
    trunc_at=None,
    # Any other keyword arguments you want applied to `show_results`
    **kwargs
):
    # grab our tokenizer and ignore token to decode
    tfm = first_blurr_tfm(learner.dls)

    hf_config = tfm.hf_config
    hf_tokenizer = tfm.hf_tokenizer
    ignore_token_id = tfm.ignore_token_id

    res = L(
        [
            (
                hf_tokenizer.decode(s[0], skip_special_tokens=True)[:trunc_at],
                hf_tokenizer.decode(s[1][s[1] != ignore_token_id], skip_special_tokens=True)[:trunc_at],
                hf_tokenizer.decode(pred[0], skip_special_tokens=True)[:trunc_at],
            )
            for s, pred in zip(samples, outs)
        ]
    )

    display_df(pd.DataFrame(res, columns=["text", "target", "prediction"])[:max_n])
    return ctxs


# Cell
@typedispatch
def show_results(
    # This typedispatched `show_results` will be called for `HF_MLMInput` typed inputs
    x: MLMTextInput,
    # Your targets
    y,
    # Your raw inputs/targets
    samples,
    # The model's predictions
    outs,
    # Your `Learner`. This is required so as to get at the Hugging Face objects for decoding them into
    # something understandable
    learner,
    # Your `show_results` context
    ctxs=None,
    # The maximum number of items to show
    max_n=6,
    # Any truncation your want applied to your decoded inputs
    trunc_at=None,
    # Any other keyword arguments you want applied to `show_results`
    **kwargs,
):
    # grab our tokenizer and ignore token to decode
    tfm = first_blurr_tfm(learner.dls)

    hf_config = tfm.hf_config
    hf_tokenizer = tfm.hf_tokenizer
    ignore_token_id = tfm.ignore_token_id

    # grab our mask token id and do-not-mask token ids
    mask_token_id = hf_tokenizer.mask_token_id

    vocab = hf_tokenizer.get_vocab()
    dnm_tok_ids = [vocab[tok] for tok in list(hf_tokenizer.special_tokens_map.values()) if vocab[tok] != mask_token_id]

    res = L()
    for s, t in zip(samples, outs):
        # exclue dnm tokens from input
        inps = [
            hf_tokenizer.decode(tok_id) if (tok_id == mask_token_id or s[1][idx] == ignore_token_id) else f"[{hf_tokenizer.decode(tok_id)}]"
            for idx, tok_id in enumerate(s[0])
            if (tok_id not in dnm_tok_ids)
        ]

        # replaced masked tokens with "[{actual_token}]"
        trgs = [
            hf_tokenizer.decode(s[0][idx]) if (tok_id == ignore_token_id) else f"[{hf_tokenizer.decode(tok_id)}]"
            for idx, tok_id in enumerate(s[1])
            if (s[0][idx] not in dnm_tok_ids)
        ]

        # same as above except we replace the [MASK] with the PREDICTED token
        preds = [
            hf_tokenizer.decode(s[0][idx]) if (tok_id == ignore_token_id) else f"[{hf_tokenizer.decode(t[0][idx])}]"
            for idx, tok_id in enumerate(s[1])
            if (s[0][idx] not in dnm_tok_ids)
        ]

        res.append((" ".join(inps[:trunc_at]).strip(), " ".join(trgs[:trunc_at]).strip(), " ".join(preds[:trunc_at]).strip()))

    display_df(pd.DataFrame(res, columns=["text", "target", "prediction"])[:max_n])
    return ctxs


# Cell
@patch
def blurr_fill_mask(
    self: Learner,
    # Your input_ids or raw text string with a `hf_tokenizer.mask_token`
    inp: Union[List[int], str],
    # The number of predictions you want to return for the [MASK]ed token
    n_preds: int = 1,
    # Any other keyword arguments you want applied to text generation
    **kwargs
):
    """For MLM models"""
    # grab the Hugging Face tokenizer from the learner's dls.tfms
    tfm = first_blurr_tfm(self.dls)

    hf_config = tfm.hf_config
    hf_tokenizer = tfm.hf_tokenizer
    tok_kwargs = tfm.tok_kwargs

    # grab the text generation kwargs
    text_gen_kwargs = tfm.text_gen_kwargs if (len(kwargs) == 0) else kwargs

    if isinstance(inp, str):
        input_ids = hf_tokenizer.encode(inp, padding=True, truncation=True, return_tensors="pt", **tok_kwargs)
    else:
        # note (10/30/2020): as of pytorch 1.7, this has to be a plain ol tensor (not a subclass of TensorBase)
        input_ids = inp.as_subclass(Tensor)

    input_ids = input_ids.to(self.model.hf_model.device)
    mask_token_index = torch.where(input_ids == hf_tokenizer.mask_token_id)[1]

    outputs = self.model.hf_model(input_ids)
    mask_token_logits = outputs.logits[0, mask_token_index, :]
    preds = torch.topk(mask_token_logits, n_preds, dim=-1).indices[0].tolist()

    outputs = [inp.replace(hf_tokenizer.mask_token, hf_tokenizer.decode([tok_id]).strip()) for tok_id in preds]

    return outputs


# Cell
@delegates(Blearner.__init__)
class BlearnerForLM(Blearner):
    def __init__(self, dls: DataLoaders, hf_model: PreTrainedModel, **kwargs):
        kwargs["loss_func"] = kwargs.get("loss_func", PreCalculatedCrossEntropyLoss())
        super().__init__(dls, hf_model, **kwargs)

    @classmethod
    def get_model_cls(self, lm_type):
        return AutoModelForCausalLM if (lm_type == LMType.CAUSAL) else AutoModelForMaskedLM

    @classmethod
    def get_metrics_cb(self):
        return LMMetricsCallback()

    @classmethod
    def from_data(
        cls,
        # Your raw dataset. Supports DataFrames, Hugging Face Datasets, as well as file paths
        # to .csv, .xlsx, .xls, and .jsonl files
        data: Union[pd.DataFrame, Path, str, List[Dict]],
        # The name or path of the pretrained model you want to fine-tune
        pretrained_model_name_or_path: Optional[Union[str, os.PathLike]],
        # The language modeling strategy (or objective)
        lm_strategy_cls: BaseLMStrategy = CausalLMStrategy,
        # The attribute in your dataset that contains your raw text
        text_attr: str = "text",
        # A function that will split your Dataset into a training and validation set
        # See [here](https://docs.fast.ai/data.transforms.html#Split) for a list of fast.ai splitters
        dblock_splitter: Optional[Callable] = None,
        # Any kwargs to pass to your `DataLoaders`
        dl_kwargs={},
        # Any kwargs to pass to your task specific `Blearner`
        learner_kwargs={},
    ):
        # if we get a path/str then we're loading something like a .csv file
        if isinstance(data, Path) or isinstance(data, str):
            content_type = mimetypes.guess_type(data)[0]
            if content_type == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet":
                data = pd.read_excel(data)
            elif content_type == "text/csv":
                data = pd.read_csv(data)
            elif content_type == "application/json":
                data = pd.read_json(data, orient="records")
            else:
                raise ValueError("'data' must be a .xlsx, .xls, .csv, or .jsonl file")

            data = pd.read_csv(data)

        # infer our datablock splitter if None
        if dblock_splitter is None:
            dblock_splitter = ColSplitter() if hasattr(data, "is_valid") else RandomSplitter()

        # get our hf objects
        lm_type = lm_strategy_cls.get_lm_type()
        model_cls = cls.get_model_cls(lm_type=lm_type)
        hf_arch, hf_config, hf_tokenizer, hf_model = NLP.get_hf_objects(pretrained_model_name_or_path, model_cls=model_cls)

        # not all architectures include a native pad_token (e.g., gpt2, ctrl, etc...), so we add one here
        if hf_tokenizer.pad_token is None:
            hf_tokenizer.add_special_tokens({"pad_token": "<pad>"})
            hf_config.pad_token_id = hf_tokenizer.get_vocab()["<pad>"]
            hf_model.resize_token_embeddings(len(hf_tokenizer))

        # define DataBlock and DataLoaders
        bbtfm = LMBatchTokenizeTransform(hf_arch, hf_config, hf_tokenizer, hf_model, lm_strategy_cls=lm_strategy_cls)

        input_return_type = CausalLMTextInput if (lm_type == LMType.CAUSAL) else MLMTextInput
        blocks = (TextBlock(batch_tokenize_tfm=bbtfm, input_return_type=input_return_type), noop)

        dblock = DataBlock(blocks=blocks, get_x=ItemGetter(text_attr), splitter=dblock_splitter)
        dls = dblock.dataloaders(data, **dl_kwargs.copy())

        # return BLearner instance with default metrics (optional)
        learner_kwargs["metrics"] = learner_kwargs.pop("metrics", [perplexity])
        return cls(dls, hf_model, **learner_kwargs.copy())