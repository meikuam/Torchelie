import random
import multiprocessing

import torchelie.utils as tu
from torchelie.datasets.debug import *
from .concat import HorizontalConcatDataset

import torch


class PairedDataset(torch.utils.data.Dataset):
    """
    A dataset that returns all possible pairs of samples of two datasets

    Args:
        dataset1 (Dataset): a dataset
        dataset2 (Dataset): another dataset
    """

    def __init__(self, dataset1, dataset2):
        super(PairedDataset, self).__init__()
        self.dataset1 = dataset1
        self.dataset2 = dataset2

    def __getitem__(self, i):
        idx1 = i % len(self.dataset1)
        idx2 = i // len(self.dataset2)

        x1 = self.dataset1[idx1]
        x2 = self.dataset2[idx2]

        return list(zip(x1, x2))

    def __len__(self):
        return len(self.dataset1) * len(self.dataset2)


def mixup(x1, x2, y1, y2, num_classes, mixer=None, alpha=0.4):
    r"""
    Mixes samples `x1` and `x2` with respective labels `y1` and `y2` according
    to MixUp

    :math:`\lambda \sim \text{Beta}(\alpha, \alpha)`

    :math:`x = \lambda x_1 + (1-\lambda) x_2`

    :math:`y = \lambda y_1 + (1 - \lambda) y_2`

    Args:
        x1 (tensor): sample 1
        x2 (tensor): sample 2
        y1 (tensor): label 1
        y2 (tensor): label 2
        num_classes (int): number of classes
        mixer (Distribution, optional): a distribution to sample lambda from.
            If unspecified, the distribution will be a Beta(alpha, alpha)
        alpha (float): if mixer is unspecified, used to parameterize the Beta
            distribution
    """
    if mixer is None:
        alpha = torch.tensor([alpha])
        mixer = torch.distributions.Beta(alpha, alpha)

    y1 = torch.tensor(y1)
    y2 = torch.tensor(y2)
    lam = mixer.sample(y1.shape).to(y1.device)
    y1 = torch.nn.functional.one_hot(y1, num_classes=num_classes).float().to(
        y1.device)
    y2 = torch.nn.functional.one_hot(y2, num_classes=num_classes).float().to(
        y1.device)

    return (lam * x1 + (1 - lam) * x2), (lam * y1 + (1 - lam) * y2)


class MixUpDataset(PairedDataset):
    """
    Linearly mixes two samples and labels from a dataset according to the MixUp
    algorithm

    https://arxiv.org/abs/1905.02249

    Args:
        dataset (Dataset): the dataset
        alpha (float): the alpha that parameterizes the beta distribution from
            which the blending factor is sampled
    """

    def __init__(self, dataset, alpha=0.4):
        super(MixUpDataset, self).__init__(dataset, dataset)
        alpha = torch.tensor([alpha])
        self.mixer = torch.distributions.Beta(alpha, alpha)

    def __getitem__(self, i):
        (x1, x2), (y1, y2) = super(MixUpDataset, self).__getitem__(i)

        return mixup(x1, x2, y1, y2, len(self.dataset1.classes), self.mixer)


class _Wrap:
    def __getattr__(self, name):
        return getattr(self.ds, name)


class NoexceptDataset(_Wrap):
    """
    Wrap a dataset and absorbs the exceptions it raises.  Useful in case of a
    big downloaded dataset with corrupted samples for instance.

    Args:
        ds (Dataset): a dataset
    """

    def __init__(self, ds):
        self.ds = ds

    def __len__(self):
        return len(self.ds)

    def __getitem__(self, i):
        try:
            return self.ds[i]
        except Exception as e:
            print(e)
            if i < len(self):
                return self[i + 1]
            else:
                return self[0]


class WithIndexDataset(_Wrap):
    """
    Wrap a dataset. Also returns the index of the accessed element. Original
    dataset's attributes are transparently accessible

    Args:
        ds (Dataset): A dataset
    """
    def __init__(self, ds):
        self.ds = ds

    def __getitem__(self, i):
        """
        Args:
            i (int): index

        Returns:
            A tuple (i, self.ds[i])
        """
        return i, self.ds[i]

    def __len__(self):
        return len(self.ds)


class CachedDataset(_Wrap):
    """
    Wrap a dataset. Lazily caches elements returned by the underlying dataset.

    Args:
        ds (Dataset): A dataset
        transform (Callable): transform to apply on cached elements
        device: the device on which the cache is allocated
    """
    def __init__(self, ds, transform=None, device='cpu'):
        self.ds = ds
        self.transform = transform
        self.cache = multiprocessing.Manager().list([None] * len(self.ds))
        self.device = device

    def __len__(self):
        return len(self.ds)

    def __getitem__(self, i):
        """
        Returns:
            The ith element of the underlying dataset or its cached value if
            available
        """
        if self.cache[i] is None:
            self.cache[i] = tu.send_to_device(self.ds[i], self.device,
                    non_blocking=True)

        x, *y = self.cache[i]

        if self.transform is not None:
            x = self.transform(x)

        return [x] + y
