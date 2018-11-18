from typing import List, Dict, Tuple, Iterable
import os

import numpy as np

from allennlp.data.vocabulary import Vocabulary, DEFAULT_PADDING_TOKEN, DEFAULT_OOV_TOKEN
from allennlp.common.util import START_SYMBOL, END_SYMBOL
from allennlp.common.registrable import Registrable
from allennlp.common.params import Params

from rulm.transform import Transform, TopKTransform
from rulm.beam import BeamSearch


class PerplexityState:
    def __init__(self):
        self.word_count = 0
        self.zeroprobs_count = 0
        self.unknown_count = 0
        self.avg_log_perplexity = 0.

    def add(self, word_index: int, probability: float, is_including_unk: bool, unk_index: int) -> None:
        old_word_count = self.word_count - self.zeroprobs_count - \
            (self.unknown_count if not is_including_unk else 0)
        self.word_count += 1

        if word_index == unk_index:
            self.unknown_count += 1
            if not is_including_unk:
                return

        if probability == 0.:
            self.zeroprobs_count += 1
            return

        log_prob = -np.log(probability)
        true_word_count = self.word_count - self.zeroprobs_count - \
            (self.unknown_count if not is_including_unk else 0)

        prev_avg = self.avg_log_perplexity * old_word_count / true_word_count
        self.avg_log_perplexity = prev_avg + log_prob / true_word_count
        return

    def __repr__(self):
        return "Avg ppl: {}, zeroprobs: {}, unk: {}".format(
            np.exp(self.avg_log_perplexity), self.zeroprobs_count, self.unknown_count)


class LanguageModel(Registrable):
    def __init__(self, vocabulary: Vocabulary, transforms: Tuple[Transform], reverse: bool=False):
        self.vocabulary = vocabulary  # type: Vocabulary
        self.transforms = transforms  # type: List[Transform]
        self.reverse = reverse  # type : bool

    def train(self, inputs: Iterable[List[str]], train_params: Params):
        raise NotImplementedError()

    def train_file(self, file_name: str, train_params: Params):
        raise NotImplementedError()

    def predict(self, inputs: List[int]) -> List[float]:
        raise NotImplementedError()

    def query(self, inputs: List[str]) -> Dict[str, float]:
        indices = self._numericalize_inputs(inputs)
        next_index_prediction = self.predict(indices)
        return {self.vocabulary.get_token_from_index(index): prob
                for index, prob in enumerate(next_index_prediction)}

    def beam_decoding(self, inputs: List[str], beam_width: int=5,
                      max_length: int=50, length_reward: float=0.0) -> List[str]:
        current_state = self._numericalize_inputs(inputs)
        beam = BeamSearch(
            eos_index=self.vocabulary.get_token_index(END_SYMBOL),
            predict_func=self.predict,
            transforms=self.transforms,
            beam_width=beam_width,
            max_length=max_length,
            length_reward=length_reward)
        best_guess = beam.decode(current_state)
        return self._decipher_outputs(best_guess)

    def sample_decoding(self, inputs: List[str], k: int=5, max_length: int=30) -> List[str]:
        vocab_size = self.vocabulary.get_vocab_size()
        if k > vocab_size:
            k = vocab_size
        current_state = self._numericalize_inputs(inputs)
        bos_index = self.vocabulary.get_token_index(START_SYMBOL)
        eos_index = self.vocabulary.get_token_index(END_SYMBOL)
        last_index = current_state[-1] if current_state else bos_index
        while last_index != eos_index and len(current_state) < max_length:
            next_word_probabilities = self.predict(current_state)
            for transform in self.transforms:
                next_word_probabilities = transform(next_word_probabilities)
            next_word_probabilities = TopKTransform(k)(next_word_probabilities)
            last_index = self._choose(next_word_probabilities)[0]
            for transform in self.transforms:
                transform.advance(last_index)
            current_state.append(last_index)
        outputs = self._decipher_outputs(current_state)
        return outputs

    def measure_perplexity(self, inputs: List[List[str]], state: PerplexityState,
                           is_including_unk: bool=True) -> PerplexityState:
        for sentence in inputs:
            indices = self._numericalize_inputs(sentence)
            indices.append(self.vocabulary.get_token_index(END_SYMBOL))
            for i, word_index in enumerate(indices[1:]):
                context = indices[:i+1]

                prediction = self.predict(context)
                unk_index = self.vocabulary.get_token_index(DEFAULT_OOV_TOKEN)
                state.add(word_index, prediction[word_index], is_including_unk, unk_index)
        return state

    def measure_perplexity_file(self, file_name, batch_size: int=100):
        assert os.path.exists(file_name)
        sentences = []
        ppl_state = PerplexityState()
        batch_number = 0
        with open(file_name, "r", encoding="utf-8") as r:
            for line in r:
                words = line.strip().split()
                sentences.append(words)
                if len(sentences) == batch_size:
                    ppl_state = self.measure_perplexity(sentences, ppl_state)
                    batch_number += 1
                    print("Measure_perplexity: {} sentences processed, {}".format(
                        batch_number * batch_size, ppl_state))
                    sentences = []
            if sentences:
                ppl_state = self.measure_perplexity(sentences, ppl_state)
        return ppl_state

    @staticmethod
    def _parse_file_for_train(file_name):
        assert os.path.exists(file_name)
        with open(file_name, "r", encoding="utf-8") as r:
            for line in r:
                words = line.strip().split()
                yield words

    def _numericalize_inputs(self, words: List[str]) -> List[int]:
        if self.reverse:
            words = words[::-1]
        words.insert(0, START_SYMBOL)
        return [self.vocabulary.get_token_index(word) for word in words]

    def _decipher_outputs(self, indices: List[int]) -> List[str]:
        return [self.vocabulary.get_token_from_index(index) for index in indices[1:-1]]

    @staticmethod
    def _choose(model: np.array, k: int=1):
        norm_model = model / np.sum(model)
        return np.random.choice(range(norm_model.shape[0]), k, p=norm_model, replace=False)


class EquiprobableLanguageModel(LanguageModel):
    def __init__(self, vocabulary: Vocabulary, transforms: Tuple[Transform]=tuple()):
        LanguageModel.__init__(self, vocabulary, transforms)

    def train(self, inputs: List[List[str]]):
        pass

    def train_file(self, file_name: str):
        pass

    def normalize(self):
        pass

    def predict(self, inputs: List[int]):
        vocab_size = self.vocabulary.get_vocab_size()
        probabilities = np.full((vocab_size,), 1./(vocab_size-2))
        probabilities[self.vocabulary.get_token_index(START_SYMBOL)] = 0.
        probabilities[self.vocabulary.get_token_index(DEFAULT_PADDING_TOKEN)] = 0.
        return probabilities


class VocabularyChainLanguageModel(LanguageModel):
    def __init__(self, vocabulary: Vocabulary, transforms: Tuple[Transform]=tuple()):
        LanguageModel.__init__(self, vocabulary, transforms)

    def train(self, inputs: List[List[str]]):
        pass

    def train_file(self, file_name: str):
        pass

    def normalize(self):
        pass

    def predict(self, inputs: List[int]):
        probabilities = np.zeros(self.vocabulary.get_vocab_size())
        last_index = inputs[-1]
        aux = (START_SYMBOL, END_SYMBOL, DEFAULT_OOV_TOKEN, DEFAULT_PADDING_TOKEN)
        aux_indices = [self.vocabulary.get_token_index(s) for s in aux]
        first_not_aux_index = 0
        for i in range(self.vocabulary.get_vocab_size()):
            if i in aux_indices:
                continue
            first_not_aux_index = i
            break
        bos_index = aux_indices[0]
        if last_index == bos_index:
            probabilities[first_not_aux_index] = 1.
        elif last_index != self.vocabulary.get_vocab_size() - 1:
            probabilities[last_index + 1] = 1.
        return probabilities
