# -*- coding: utf-8 -*-
"""
Created on Wed Nov 02 19:43:57 2016

@author: yamane
"""

import numpy as np
import time
import copy
import tqdm
import h5py
import matplotlib.pyplot as plt
from skimage import io, transform
from multiprocessing import Process, Queue
from chainer import cuda, optimizers, Chain, serializers
import chainer.functions as F
import chainer.links as L
import cv2


# ネットワークの定義
class Convnet(Chain):
    def __init__(self):
        super(Convnet, self).__init__(
            conv1=L.Convolution2D(3, 64, 3, stride=2, pad=1),
            norm1=L.BatchNormalization(64),
            conv2=L.Convolution2D(64, 64, 3, stride=2, pad=1),
            norm2=L.BatchNormalization(64),
            conv3=L.Convolution2D(64, 128, 3, stride=2, pad=1),
            norm3=L.BatchNormalization(128),
            conv4=L.Convolution2D(128, 128, 3, stride=2, pad=1),
            norm4=L.BatchNormalization(128),
            conv5=L.Convolution2D(128, 256, 3, stride=2, pad=1),
            norm5=L.BatchNormalization(256),
            conv6=L.Convolution2D(256, 256, 3, stride=2, pad=1),
            norm6=L.BatchNormalization(256),

            l1=L.Linear(4096, 1000),
            norm7=L.BatchNormalization(1000),
            l2=L.Linear(1000, 1),
        )

    def network(self, X, test):
        h = F.relu(self.norm1(self.conv1(X), test=test))
        h = F.relu(self.norm2(self.conv2(h), test=test))
        h = F.relu(self.norm3(self.conv3(h), test=test))
        h = F.relu(self.norm4(self.conv4(h), test=test))
        h = F.relu(self.norm5(self.conv5(h), test=test))
        h = F.relu(self.norm6(self.conv6(h), test=test))
        h = F.relu(self.norm7(self.l1(h), test=test))
        y = self.l2(h)
        return y

    def forward(self, X, test):
        y = self.network(X, test)
        return y

    def lossfun(self, X, t, test):
        y = self.forward(X, test)
        loss = F.sigmoid_cross_entropy(y, t)
        accuracy = F.binary_accuracy(y, t)
        return loss, accuracy

    def loss_ave(self, queue, num_batches, test):
        losses = []
        accuracies = []
        for i in range(num_batches):
            X_batch, T_batch = queue.get()
            X_batch = cuda.to_gpu(X_batch)
            T_batch = cuda.to_gpu(T_batch)
            loss, accuracy = self.lossfun(X_batch, T_batch, test)
            losses.append(cuda.to_cpu(loss.data))
            accuracies.append(cuda.to_cpu(accuracy.data))
        return np.mean(losses), np.mean(accuracies)


def mini_batch_train(queue, num_train, batch_size, file_path, min_ratio,
                     max_ratio, crop_size, output_size):
    dataset = h5py.File(file_path)
    image_features = dataset['image_features']
    r_min = min_ratio
    r_max = max_ratio

    data = range(0, num_train)
    num_batches = num_train / batch_size

    while True:
        for indexes in np.array_split(data, num_batches):
            images = []
            ts = []
            image_batch = image_features[indexes.tolist()]
            for i in range(len(indexes)):
                image = image_batch[i]
                t = np.random.choice(2)
                if t == 1:
                    r = sample_random_aspect_ratio(r_max, r_min)
                else:
                    r = 1
                image = change_aspect_ratio(image, r)
                square_image = crop_center(image)
                resize_image = cv2.resize(square_image,
                                          (output_size, output_size))
                resize_image = random_crop_and_flip(resize_image, crop_size)
                images.append(resize_image)
                ts.append(t)
            X = np.stack(images, axis=0)
            X = np.transpose(X, (0, 3, 1, 2))
            X = X.astype(np.float32)
            T = np.array(ts, dtype=np.int32).reshape(-1, 1)

            queue.put((X, T))


def mini_batch_test(queue, num_test, batch_size, file_path, min_ratio,
                    max_ratio, crop_size, output_size):
    dataset = h5py.File(file_path)
    image_features = dataset['image_features']
    r_min = min_ratio
    r_max = max_ratio

    end = image_features.shape[0]
    data = range(end - num_test, end)
    num_batches = num_test / batch_size

    while True:
        for indexes in np.array_split(data, num_batches):
            images = []
            ts = []
            image_batch = image_features[indexes.tolist()]
            for i in range(len(indexes)):
                image = image_batch[i]
                t = np.random.choice(2)
                if t == 1:
                    r = sample_random_aspect_ratio(r_max, r_min)
                else:
                    r = 1
                image = change_aspect_ratio(image, r)
                square_image = crop_center(image)
                resize_image = cv2.resize(square_image,
                                          (output_size, output_size))
                resize_image = random_crop_and_flip(resize_image, crop_size)
                images.append(resize_image)
                ts.append(t)
            X = np.stack(images, axis=0)
            X = np.transpose(X, (0, 3, 1, 2))
            X = X.astype(np.float32)
            T = np.array(ts, dtype=np.int32).reshape(-1, 1)

            queue.put((X, T))


def change_aspect_ratio(image, aspect_ratio):
    h_image, w_image = image.shape[:2]
    r = aspect_ratio

    if r == 1:
        return image
    elif r > 1:
        w_image = int(w_image * r)
    else:
        h_image = int(h_image / float(r))
    # cv2.resize:（image, (w, h))
    # transform.resize:(image, (h, w))
    resize_image = cv2.resize(image, (h_image, w_image))
    return resize_image


def crop_center(image):
    height, width = image.shape[:2]
    left = 0
    right = width
    top = 0
    bottom = height

    if height >= width:  # 縦長の場合
        output_size = width
        margin = int((height - width) / 2)
        top = margin
        bottom = top + output_size
    else:  # 横長の場合
        output_size = height
        margin = int((width - height) / 2)
        left = margin
        right = left + output_size

    square_image = image[top:bottom, left:right]

    return square_image


def padding_image(image):
    height, width = image.shape[:2]
    left = 0
    right = width
    top = 0
    bottom = height

    if height >= width:  # 縦長の場合
        output_size = height
        margin = int((height - width) / 2)
        left = margin
        right = left + width
    else:  # 横長の場合
        output_size = width
        margin = int((width - height) / 2)
        top = margin
        bottom = top + height

    square_image = np.zeros((output_size, output_size, 1))
    square_image[top:bottom, left:right] = image

    return square_image


def random_crop_and_flip(image, crop_size):
    h_image, w_image = image.shape[:2]
    h_crop = crop_size
    w_crop = crop_size

    # 0以上 h_image - h_crop以下の整数乱数
    top = np.random.randint(0, h_image - h_crop + 1)
    left = np.random.randint(0, w_image - w_crop + 1)
    bottom = top + h_crop
    right = left + w_crop

    image = image[top:bottom, left:right]

    if np.random.rand() > 0.5:  # 半々の確率で
        image = image[:, ::-1]  # 左右反転

    return image


def transpose(image):
    if image.ndim == 2:
        image = image.T
    else:
        image = np.transpose(image, (1, 0, 2))
    return image


def sample_random_aspect_ratio(r_max, r_min=1):
    # アスペクト比rをランダム生成する
    r = np.random.uniform(r_min, r_max)
    if np.random.rand() > 0.5:
        r = 1 / r
    return r


def fix_image(image, aspect_ratio):
    image_size = image.shape[2]
    r = 1 / aspect_ratio
    image = image.reshape(-1, image_size, image_size)
    image = np.transpose(image, (1, 2, 0))
    fix_image = change_aspect_ratio(image, r)
    fix_image = crop_center(fix_image)
    fix_image = np.transpose(fix_image, (2, 0, 1))
    return fix_image


if __name__ == '__main__':
    # 超パラメータ
    max_iteration = 50  # 繰り返し回数
    batch_size = 100  # ミニバッチサイズ
    num_train = 20000
    num_test = 100
    learning_rate = 0.0001
    output_size = 256
    crop_size = 224
    aspect_ratio_max = 4
    aspect_ratio_min = 2
    file_path = r'E:\stanford_Dogs_Dataset\raw_dataset_binary\output_size_500\output_size_500.hdf5'

    queue_train = Queue(10)
    process_train = Process(target=mini_batch_train,
                            args=(queue_train, num_train, batch_size,
                                  file_path, aspect_ratio_min,
                                  aspect_ratio_max, crop_size, output_size))
    process_train.start()
    queue_test = Queue(10)
    process_test = Process(target=mini_batch_test,
                           args=(queue_test, num_test, batch_size,
                                 file_path, aspect_ratio_min, aspect_ratio_max,
                                 crop_size, output_size))
    process_test.start()

    model = Convnet().to_gpu()
    # Optimizerの設定
    optimizer = optimizers.Adam(learning_rate)
    optimizer.setup(model)

    image_list = []
    epoch_loss = []
    epoch_valid_loss = []
    epoch_accuracy = []
    epoch_valid_accuracy = []
    loss_valid_best = np.inf
    accuracy_valid_best = 0

    num_batches_train = num_train / batch_size
    num_batches_test = num_test / batch_size

    time_origin = time.time()
    try:
        for epoch in range(max_iteration):
            time_begin = time.time()
            losses = []
            accuracies = []
            for i in tqdm.tqdm(range(num_batches_train)):
                X_batch, T_batch = queue_train.get()
                X_batch = cuda.to_gpu(X_batch)
                T_batch = cuda.to_gpu(T_batch)
                # 勾配を初期化
                optimizer.zero_grads()
                # 順伝播を計算し、誤差と精度を取得
                loss, accuracy = model.lossfun(X_batch, T_batch, False)
                # 逆伝搬を計算
                loss.backward()
                optimizer.update()
                losses.append(cuda.to_cpu(loss.data))
                accuracies.append(cuda.to_cpu(accuracy.data))

            time_end = time.time()
            epoch_time = time_end - time_begin
            total_time = time_end - time_origin
            epoch_loss.append(np.mean(losses))
            epoch_accuracy.append(np.mean(accuracies))

            loss_valid, accuracy_valid = model.loss_ave(queue_test,
                                                        num_batches_test,
                                                        True)

            epoch_valid_loss.append(loss_valid)
            epoch_valid_accuracy.append(accuracy_valid)

            if loss_valid < loss_valid_best:
                loss_valid_best = loss_valid
                epoch__loss_best = epoch
            if accuracy_valid > accuracy_valid_best:
                accuracy_valid_best = accuracy_valid
                epoch_accuracy_best = epoch

            # 訓練データでの結果を表示
            print "epoch:", epoch
            print "time", epoch_time, "(", total_time, ")"
            print "loss[train]:", epoch_loss[epoch]
            print "loss[valid]:", loss_valid
            print "accuracy[train]:", epoch_accuracy[epoch]
            print "accuracy[valid]:", accuracy_valid
            print "loss[valid_best]:", loss_valid_best
            print "accuracy[valid_best]:", accuracy_valid_best

            plt.plot(epoch_loss)
            plt.plot(epoch_valid_loss)
            plt.title("loss")
            plt.legend(["train", "valid"], loc="upper right")
            plt.grid()
            plt.show()

            plt.plot(epoch_accuracy)
            plt.plot(epoch_valid_accuracy)
            plt.title("accuracy")
            plt.legend(["train", "valid"], loc="lower right")
            plt.grid()
            plt.show()

    except KeyboardInterrupt:
        print "割り込み停止が実行されました"

    process_train.terminate()
    process_test.terminate()
    print 'max_iteration:', max_iteration
    print 'learning_rate:', learning_rate
    print 'batch_size:', batch_size
    print 'train_size', num_train
    print 'valid_size', num_test
