# Copyright (c) OpenMMLab. All rights reserved.
from typing import List, Optional, Sequence, Union

import mmengine
import numpy as np
import torch
import torch.nn.functional as F
from mmengine.evaluator import BaseMetric

from mmcls.registry import METRICS


def to_tensor(value):
    """Convert value to torch.Tensor."""
    if isinstance(value, np.ndarray):
        value = torch.from_numpy(value)
    elif isinstance(value, Sequence) and not mmengine.is_str(value):
        value = torch.tensor(value)
    elif not isinstance(value, torch.Tensor):
        raise TypeError(f'{type(value)} is not an available argument.')
    return value


def _precision_recall_f1_support(pred_positive, gt_positive, average):
    """calculate base classification task metrics, such as  precision, recall,
    f1_score, support."""
    average_options = ['micro', 'macro', None]
    assert average in average_options, 'Invalid `average` argument, ' \
        f'please specicy from {average_options}.'

    class_correct = (pred_positive & gt_positive)
    if average == 'micro':
        tp_sum = class_correct.sum()
        pred_sum = pred_positive.sum()
        gt_sum = gt_positive.sum()
    else:
        tp_sum = class_correct.sum(0)
        pred_sum = pred_positive.sum(0)
        gt_sum = gt_positive.sum(0)

    precision = tp_sum / torch.clamp(pred_sum, min=1.) * 100
    recall = tp_sum / torch.clamp(gt_sum, min=1.) * 100
    f1_score = 2 * precision * recall / torch.clamp(
        precision + recall, min=torch.finfo(torch.float32).eps)
    if average in ['macro', 'micro']:
        precision = precision.mean(0)
        recall = recall.mean(0)
        f1_score = f1_score.mean(0)
        support = gt_sum.sum(0)
    else:
        support = gt_sum
    return precision, recall, f1_score, support


@METRICS.register_module()
class Accuracy(BaseMetric):
    """Top-k accuracy evaluation metric.

    Args:
        topk (int | Sequence[int]): If the predictions in ``topk``
            matches the target, the predictions will be regarded as
            correct ones. Defaults to 1.
        thrs (Sequence[float | None] | float | None): Predictions with scores
            under the thresholds are considered negative. None means no
            thresholds. Defaults to 0.
        collect_device (str): Device name used for collecting results from
            different ranks during distributed training. Must be 'cpu' or
            'gpu'. Defaults to 'cpu'.
        prefix (str, optional): The prefix that will be added in the metric
            names to disambiguate homonymous metrics of different evaluators.
            If prefix is not provided in the argument, self.default_prefix
            will be used instead. Defaults to None.

    Examples:
        >>> import torch
        >>> from mmcls.metrics import Accuracy
        >>> # -------------------- The Basic Usage --------------------
        >>> y_pred = [0, 2, 1, 3]
        >>> y_true = [0, 1, 2, 3]
        >>> Accuracy.calculate(y_pred, y_true)
        tensor([50.])
        >>> # Calculate the top1 and top5 accuracy.
        >>> y_score = torch.rand((1000, 10))
        >>> y_true = torch.zeros((1000, ))
        >>> Accuracy.calculate(y_score, y_true, topk=(1, 5))
        [[tensor([9.9000])], [tensor([51.5000])]]
        >>>
        >>> # ------------------- Use with Evalutor -------------------
        >>> from mmcls.core import ClsDataSample
        >>> from mmengine.evaluator import Evaluator
        >>> data_batch = [{
        ...     'inputs': None,  # In this example, the `inputs` is not used.
        ...     'data_sample': ClsDataSample().set_gt_label(0)
        ... } for i in range(1000)]
        >>> pred = [
        ...     ClsDataSample().set_pred_score(torch.rand(10))
        ...     for i in range(1000)
        ... ]
        >>> evaluator = Evaluator(metrics=Accuracy(topk=(1, 5)))
        >>> evaluator.process(data_batch, pred)
        >>> evaluator.evaluate(1000)
        {
            'accuracy/top1': 9.300000190734863,
            'accuracy/top5': 51.20000076293945
        }
    """
    default_prefix: Optional[str] = 'accuracy'

    def __init__(self,
                 topk: Union[int, Sequence[int]] = (1, ),
                 thrs: Union[float, Sequence[Union[float, None]], None] = 0.,
                 collect_device: str = 'cpu',
                 prefix: Optional[str] = None) -> None:
        super().__init__(collect_device=collect_device, prefix=prefix)

        if isinstance(topk, int):
            self.topk = (topk, )
        else:
            self.topk = tuple(topk)

        if isinstance(thrs, float) or thrs is None:
            self.thrs = (thrs, )
        else:
            self.thrs = tuple(thrs)

    def process(self, data_batch: Sequence[dict], predictions: Sequence[dict]):
        """Process one batch of data and predictions.

        The processed results should be stored in ``self.results``, which will
        be used to computed the metrics when all batches have been processed.

        Args:
            data_batch (Sequence[dict]): A batch of data from the dataloader.
            predictions (Sequence[dict]): A batch of outputs from the model.
        """

        for data, pred in zip(data_batch, predictions):
            result = dict()
            pred_label = pred['pred_label']
            # Use gt_label in the pred dict preferentially.
            gt_label = pred.get('gt_label', data['data_sample']['gt_label'])
            if 'score' in pred_label:
                result['pred_score'] = pred_label['score'].cpu()
            else:
                result['pred_label'] = pred_label['label'].cpu()
            result['gt_label'] = gt_label['label'].cpu()
            # Save the result to `self.results`.
            self.results.append(result)

    def compute_metrics(self, results: List):
        """Compute the metrics from processed results.

        Args:
            results (dict): The processed results of each batch.

        Returns:
            Dict: The computed metrics. The keys are the names of the metrics,
            and the values are corresponding results.
        """
        # NOTICE: don't access `self.results` from the method.
        metrics = {}

        # concat
        target = torch.cat([res['gt_label'] for res in results])
        if 'pred_score' in results[0]:
            pred = torch.stack([res['pred_score'] for res in results])

            try:
                acc = self.calculate(pred, target, self.topk, self.thrs)
            except ValueError as e:
                # If the topk is invalid.
                raise ValueError(
                    str(e) + ' Please check the `val_evaluator` and '
                    '`test_evaluator` fields in your config file.')

            multi_thrs = len(self.thrs) > 1
            for i, k in enumerate(self.topk):
                for j, thr in enumerate(self.thrs):
                    name = f'top{k}'
                    if multi_thrs:
                        name += '_no-thr' if thr is None else f'_thr-{thr:.2f}'
                    metrics[name] = acc[i][j].item()
        else:
            # If only label in the `pred_label`.
            pred = torch.cat([res['pred_label'] for res in results])
            acc = self.calculate(pred, target, self.topk, self.thrs)
            metrics['top1'] = acc.item()

        return metrics

    @staticmethod
    def calculate(
        pred: Union[torch.Tensor, np.ndarray, Sequence],
        target: Union[torch.Tensor, np.ndarray, Sequence],
        topk: Sequence[int] = (1, ),
        thrs: Sequence[Union[float, None]] = (0., ),
    ) -> Union[torch.Tensor, List[List[torch.Tensor]]]:
        """Calculate the accuracy.

        Args:
            pred (torch.Tensor | np.ndarray | Sequence): The prediction
                results. It can be labels (N, ), or scores of every
                class (N, C).
            target (torch.Tensor | np.ndarray | Sequence): The target of
                each prediction with shape (N, ).
            thrs (Sequence[float | None]): Predictions with scores under
                the thresholds are considered negative. It's only used
                when ``pred`` is scores. None means no thresholds.
                Defaults to (0., ).
            thrs (Sequence[float]): Predictions with scores under
                the thresholds are considered negative. It's only used
                when ``pred`` is scores. Defaults to (0., ).

        Returns:
            torch.Tensor | List[List[torch.Tensor]]: Accuracy.

            - torch.Tensor: If the ``pred`` is a sequence of label instead of
              score (number of dimensions is 1). Only return a top-1 accuracy
              tensor, and ignore the argument ``topk` and ``thrs``.
            - List[List[torch.Tensor]]: If the ``pred`` is a sequence of score
              (number of dimensions is 2). Return the accuracy on each ``topk``
              and ``thrs``. And the first dim is ``topk``, the second dim is
              ``thrs``.
        """

        pred = to_tensor(pred)
        target = to_tensor(target).to(torch.int64)
        num = pred.size(0)
        assert pred.size(0) == target.size(0), \
            f"The size of pred ({pred.size(0)}) doesn't match "\
            f'the target ({target.size(0)}).'

        if pred.ndim == 1:
            # For pred label, ignore topk and acc
            pred_label = pred.int()
            correct = pred.eq(target).float().sum(0, keepdim=True)
            acc = correct.mul_(100. / num)
            return acc
        else:
            # For pred score, calculate on all topk and thresholds.
            pred = pred.float()
            maxk = max(topk)

            if maxk > pred.size(1):
                raise ValueError(
                    f'Top-{maxk} accuracy is unavailable since the number of '
                    f'categories is {pred.size(1)}.')

            pred_score, pred_label = pred.topk(maxk, dim=1)
            pred_label = pred_label.t()
            correct = pred_label.eq(target.view(1, -1).expand_as(pred_label))
            results = []
            for k in topk:
                results.append([])
                for thr in thrs:
                    # Only prediction values larger than thr are counted
                    # as correct
                    _correct = correct
                    if thr is not None:
                        _correct = _correct & (pred_score.t() > thr)
                    correct_k = _correct[:k].reshape(-1).float().sum(
                        0, keepdim=True)
                    acc = correct_k.mul_(100. / num)
                    results[-1].append(acc)
            return results


@METRICS.register_module()
class SingleLabelMetric(BaseMetric):
    """A collection of metrics for single-label multi-class classification task
    based on confusion matrix.

    It includes precision, recall, f1-score and support. Comparing with
    :class:`Accuracy`, these metrics doesn't support topk, but supports
    various average mode.

    Args:
        thrs (Sequence[float | None] | float | None): Predictions with scores
            under the thresholds are considered negative. None means no
            thresholds. Defaults to 0.
        items (Sequence[str]): The detailed metric items to evaluate. Here is
            the available options:

                - `"precision"`: The ratio tp / (tp + fp) where tp is the
                  number of true positives and fp the number of false
                  positives.
                - `"recall"`: The ratio tp / (tp + fn) where tp is the number
                  of true positives and fn the number of false negatives.
                - `"f1-score"`: The f1-score is the harmonic mean of the
                  precision and recall.
                - `"support"`: The total number of occurrences of each category
                  in the target.

            Defaults to ('precision', 'recall', 'f1-score').
        average (str, optional): The average method. If None, the scores
            for each class are returned. And it supports two average modes:

                - `"macro"`: Calculate metrics for each category, and calculate
                  the mean value over all categories.
                - `"micro"`: Calculate metrics globally by counting the total
                  true positives, false negatives and false positives.

            Defaults to "macro".
        collect_device (str): Device name used for collecting results from
            different ranks during distributed training. Must be 'cpu' or
            'gpu'. Defaults to 'cpu'.
        prefix (str, optional): The prefix that will be added in the metric
            names to disambiguate homonymous metrics of different evaluators.
            If prefix is not provided in the argument, self.default_prefix
            will be used instead. Defaults to None.

    Examples:
        >>> import torch
        >>> from mmcls.metrics import SingleLabelMetric
        >>> # -------------------- The Basic Usage --------------------
        >>> y_pred = [0, 1, 1, 3]
        >>> y_true = [0, 2, 1, 3]
        >>> # Output precision, recall, f1-score and support.
        >>> SingleLabelMetric.calculate(y_pred, y_true, num_classes=4)
        (tensor(62.5000, dtype=torch.float64),
         tensor(75., dtype=torch.float64),
         tensor(66.6667, dtype=torch.float64),
         tensor(4))
        >>> # Calculate with different thresholds.
        >>> y_score = torch.rand((1000, 10))
        >>> y_true = torch.zeros((1000, ))
        >>> SingleLabelMetric.calculate(y_score, y_true, thrs=(0., 0.9))
        [(tensor(10., dtype=torch.float64),
          tensor(1.2100, dtype=torch.float64),
          tensor(2.1588, dtype=torch.float64),
          tensor(1000)),
         (tensor(10., dtype=torch.float64),
          tensor(0.8200, dtype=torch.float64),
          tensor(1.5157, dtype=torch.float64),
          tensor(1000))]
        >>>
        >>> # ------------------- Use with Evalutor -------------------
        >>> from mmcls.core import ClsDataSample
        >>> from mmengine.evaluator import Evaluator
        >>> data_batch = [{
        ...     'inputs': None,  # In this example, the `inputs` is not used.
        ...     'data_sample': ClsDataSample().set_gt_label(i%5)
        ... } for i in range(1000)]
        >>> pred = [
        ...     ClsDataSample().set_pred_score(torch.rand(5))
        ...     for i in range(1000)
        ... ]
        >>> evaluator = Evaluator(metrics=SingleLabelMetric())
        >>> evaluator.process(data_batch, pred)
        >>> evaluator.evaluate(1000)
        {
            'single-label/precision': 10.0,
            'single-label/recall': 0.96,
            'single-label/f1-score': 1.7518248175182483
        }
        >>> # Evaluate on each class
        >>> evaluator = Evaluator(metrics=SingleLabelMetric(average=None))
        >>> evaluator.process(data_batch, pred)
        >>> evaluator.evaluate(1000)
        {
            'single-label/precision_classwise': [21.1, 18.7, 17.8, 19.4, 16.1],
            'single-label/recall_classwise': [18.5, 18.5, 17.0, 20.0, 18.0],
            'single-label/f1-score_classwise': [19.7, 18.6, 17.1, 19.7, 17.0]
        }
    """
    default_prefix: Optional[str] = 'single-label'

    def __init__(self,
                 thrs: Union[float, Sequence[Union[float, None]], None] = 0.,
                 items: Sequence[str] = ('precision', 'recall', 'f1-score'),
                 average: Optional[str] = 'macro',
                 collect_device: str = 'cpu',
                 prefix: Optional[str] = None) -> None:
        super().__init__(collect_device=collect_device, prefix=prefix)

        if isinstance(thrs, float) or thrs is None:
            self.thrs = (thrs, )
        else:
            self.thrs = tuple(thrs)

        for item in items:
            assert item in ['precision', 'recall', 'f1-score', 'support'], \
                f'The metric {item} is not supported by `SingleLabelMetric`,' \
                ' please specicy from "precision", "recall", "f1-score" and ' \
                '"support".'
        self.items = tuple(items)
        self.average = average

    def process(self, data_batch: Sequence[dict], predictions: Sequence[dict]):
        """Process one batch of data and predictions.

        The processed results should be stored in ``self.results``, which will
        be used to computed the metrics when all batches have been processed.

        Args:
            data_batch (Sequence[dict]): A batch of data from the dataloader.
            predictions (Sequence[dict]): A batch of outputs from the model.
        """

        for data, pred in zip(data_batch, predictions):
            result = dict()
            pred_label = pred['pred_label']
            # Use gt_label in the pred dict preferentially.
            gt_label = pred.get('gt_label', data['data_sample']['gt_label'])
            if 'score' in pred_label:
                result['pred_score'] = pred_label['score'].cpu()
            elif ('num_classes' in pred_label
                  or 'num_classes' in data['data_sample']):
                result['pred_label'] = pred_label['label'].cpu()
                result['num_classes'] = pred_label.get(
                    'num_classes', None) or data['data_sample']['num_classes']
            else:
                raise ValueError('The `pred_label` in predictions do not '
                                 'have neither `score` nor `num_classes`.')
            result['gt_label'] = gt_label['label'].cpu()
            # Save the result to `self.results`.
            self.results.append(result)

    def compute_metrics(self, results: List):
        """Compute the metrics from processed results.

        Args:
            results (list): The processed results of each batch.

        Returns:
            Dict: The computed metrics. The keys are the names of the metrics,
            and the values are corresponding results.
        """
        # NOTICE: don't access `self.results` from the method. `self.results`
        # are a list of results from multiple batch, while the input `results`
        # are the collected results.
        metrics = {}

        def pack_results(precision, recall, f1_score, support):
            single_metrics = {}
            if 'precision' in self.items:
                single_metrics['precision'] = precision
            if 'recall' in self.items:
                single_metrics['recall'] = recall
            if 'f1-score' in self.items:
                single_metrics['f1-score'] = f1_score
            if 'support' in self.items:
                single_metrics['support'] = support
            return single_metrics

        # concat
        target = torch.cat([res['gt_label'] for res in results])
        if 'pred_score' in results[0]:
            pred = torch.stack([res['pred_score'] for res in results])
            metrics_list = self.calculate(
                pred, target, thrs=self.thrs, average=self.average)

            multi_thrs = len(self.thrs) > 1
            for i, thr in enumerate(self.thrs):
                if multi_thrs:
                    suffix = '_no-thr' if thr is None else f'_thr-{thr:.2f}'
                else:
                    suffix = ''

                for k, v in pack_results(*metrics_list[i]).items():
                    metrics[k + suffix] = v
        else:
            # If only label in the `pred_label`.
            pred = torch.cat([res['pred_label'] for res in results])
            res = self.calculate(
                pred,
                target,
                average=self.average,
                num_classes=results[0]['num_classes'])
            metrics = pack_results(*res)

        result_metrics = dict()
        for k, v in metrics.items():

            if self.average is None:
                result_metrics[k + '_classwise'] = v.cpu().detach().tolist()
            elif self.average == 'micro':
                result_metrics[k + f'_{self.average}'] = v.item()
            else:
                result_metrics[k] = v.item()

        return result_metrics

    @staticmethod
    def calculate(
        pred: Union[torch.Tensor, np.ndarray, Sequence],
        target: Union[torch.Tensor, np.ndarray, Sequence],
        thrs: Sequence[Union[float, None]] = (0., ),
        average: Optional[str] = 'macro',
        num_classes: Optional[int] = None,
    ) -> Union[torch.Tensor, List[torch.Tensor]]:
        """Calculate the precision, recall, f1-score and support.

        Args:
            pred (torch.Tensor | np.ndarray | Sequence): The prediction
                results. It can be labels (N, ), or scores of every
                class (N, C).
            target (torch.Tensor | np.ndarray | Sequence): The target of
                each prediction with shape (N, ).
            thrs (Sequence[float | None]): Predictions with scores under
                the thresholds are considered negative. It's only used
                when ``pred`` is scores. None means no thresholds.
                Defaults to (0., ).
            average (str, optional): The average method. If None, the scores
                for each class are returned. And it supports two average modes:

                    - `"macro"`: Calculate metrics for each category, and
                      calculate the mean value over all categories.
                    - `"micro"`: Calculate metrics globally by counting the
                      total true positives, false negatives and false
                      positives.

                Defaults to "macro".
            num_classes (Optional, int): The number of classes. If the ``pred``
                is label instead of scores, this argument is required.
                Defaults to None.

        Returns:
            Tuple: The tuple contains precision, recall and f1-score.
            And the type of each item is:

            - torch.Tensor: If the ``pred`` is a sequence of label instead of
              score (number of dimensions is 1). Only returns a tensor for
              each metric. The shape is (1, ) if ``classwise`` is False, and
              (C, ) if ``classwise`` is True.
            - List[torch.Tensor]: If the ``pred`` is a sequence of score
              (number of dimensions is 2). Return the metrics on each ``thrs``.
              The shape of tensor is (1, ) if ``classwise`` is False, and (C, )
              if ``classwise`` is True.
        """
        average_options = ['micro', 'macro', None]
        assert average in average_options, 'Invalid `average` argument, ' \
            f'please specicy from {average_options}.'

        pred = to_tensor(pred)
        target = to_tensor(target).to(torch.int64)
        assert pred.size(0) == target.size(0), \
            f"The size of pred ({pred.size(0)}) doesn't match "\
            f'the target ({target.size(0)}).'

        if pred.ndim == 1:
            assert num_classes is not None, \
                'Please specicy the `num_classes` if the `pred` is labels ' \
                'intead of scores.'
            gt_positive = F.one_hot(target.flatten(), num_classes)
            pred_positive = F.one_hot(pred.to(torch.int64), num_classes)
            return _precision_recall_f1_support(pred_positive, gt_positive,
                                                average)
        else:
            # For pred score, calculate on all thresholds.
            num_classes = pred.size(1)
            pred_score, pred_label = torch.topk(pred, k=1)
            pred_score = pred_score.flatten()
            pred_label = pred_label.flatten()

            gt_positive = F.one_hot(target.flatten(), num_classes)

            results = []
            for thr in thrs:
                pred_positive = F.one_hot(pred_label, num_classes)
                if thr is not None:
                    pred_positive[pred_score <= thr] = 0
                results.append(
                    _precision_recall_f1_support(pred_positive, gt_positive,
                                                 average))

            return results