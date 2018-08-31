# -*- coding:utf-8 -*-

import tensorflow as tf
tf.set_random_seed(1993)

import utils_lenet
import utils_nin
import utils_resnet

import numpy as np
np.random.seed(1993)
import os
import pprint
import utils_mine
import visualize_result

from sklearn.metrics import confusion_matrix

from wgan.model_32x32 import GAN

flags = tf.app.flags

# 数据集的相关常量
flags.DEFINE_string("dataset", "cifar-100", "The name of dataset [cifar-10, cifar-100]")

# Generator: W-GAN_GP
flags.DEFINE_integer("dim", 128, "This overfits substantially; you're probably better off with 64 [128]")
flags.DEFINE_integer("lambda_param", 10, "Gradient penalty lambda hyperparameter [10]")
flags.DEFINE_integer("critic_iters", 5, "How many critic iterations per generator iteration [5]")
flags.DEFINE_integer("batch_size", 64, "The size of batch images [64]")
flags.DEFINE_integer("iters", 10000, "How many generator epochs to train for [64]")
flags.DEFINE_integer("output_dim", 3072, "Number of pixels in CIFAR10 (3*32*32) [3072]")
flags.DEFINE_string("mode", 'wgan-gp', "Valid options are dcgan, wgan, or wgan-gp")
flags.DEFINE_string("result_dir_wgan", 'result_wgan', "")
flags.DEFINE_integer("gan_save_interval", 500, 'interval to save a checkpoint(number of iters)')
flags.DEFINE_float("adam_lr", 1e-3, 'default: 1e-4')
flags.DEFINE_float("adam_beta1", 0.5, 'default: 0.5')
flags.DEFINE_float("adam_beta2", 0.9, 'default: 0.9')
flags.DEFINE_boolean("gan_finetune", False, 'if gan finetuned from the pre-trained model on all classes')
flags.DEFINE_integer("gan_finetune_from", -1,
                     'finetune from which iteration(-1 for final model: folder name is "final")')
flags.DEFINE_string("pretrained_model_base_dir", 'result_wgan_all_classes',
                     'if gan finetuned from the pre-trained model on all classes')
flags.DEFINE_string("pretrained_model_sub_dir", 'cifar-10/0.0001/200000/all_classes',
                     'if gan finetuned from the pre-trained model on all classes')

flags.DEFINE_boolean("only_gen_no_cls", False, "")

# 超参数选取
flags.DEFINE_boolean('use_momentum', True, '优化算法是否加冲量，如果不加的话是GradientDescent')
flags.DEFINE_float('momentum', 0.9, '')

flags.DEFINE_integer('epochs_per_category', 70, '每个类别的epoch次数')
flags.DEFINE_integer('train_batch_size', 128, '训练的batch size')
flags.DEFINE_integer('test_batch_size', 128, 'test的batch size')

# 训练的相关常量
flags.DEFINE_float('base_lr', 0.01, 'lenet: 0.01, nin: 0.1, resnet: 0.1')
flags.DEFINE_float('weight_decay', 0.00001, '0.00001, resnet: 0.002')
flags.DEFINE_float('lr_factor', 5., '')
flags.DEFINE_integer('display_interval', 20, '')
flags.DEFINE_integer('test_interval', 100, '')
lr_strat = [49, 63]

# 其他参数选取
flags.DEFINE_string('result_dir', 'result/', '')

# Network architecture
flags.DEFINE_string('network_arch', 'lenet', 'lenet, resnet, nin')
flags.DEFINE_boolean('use_dropout', True, 'only for lenet')
flags.DEFINE_integer('num_resblocks', 5, 'number of resblocks when ResNet is used')
flags.DEFINE_boolean('use_softmax', True, 'True: softmax; False: sigmoid')

# DEBUG
flags.DEFINE_integer('from_class_idx', 0, 'starting category_idx')
flags.DEFINE_integer('to_class_idx', 99, 'ending category_idx')

# Init params when new nodes added
flags.DEFINE_string('init_strategy', 'no', 'no | last | all')

# PROTO
flags.DEFINE_integer('num_exemplars_per_class', 20, '')
flags.DEFINE_string('exemplar_select_criterion', 'high', 'high | low | random')
flags.DEFINE_float('proto_weight', 1., 'the weight of the proto')
flags.DEFINE_float('gen_weight', 1., 'the weight of the generated samples')

# PROTO AUTO
flags.DEFINE_boolean('auto_choose_num_exemplars', False, 'choose the samples with the highest prob as exemplars or not')
flags.DEFINE_boolean('memory_constrained', True, '')
flags.DEFINE_integer('memory_upperbound', 2000, '2000 for CIFAR-100(to make fair comparison with iCaRL')
flags.DEFINE_float('auto_param1', 39, '')
flags.DEFINE_float('auto_param2', 8, '')

# Accelerate
flags.DEFINE_boolean('use_cache_for_gen_samples', False, 'use GANs once to generate samples for each class to accelerate')
flags.DEFINE_boolean('cache_size_per_class', 1000, 'max samples generated')

# Generate more and select distinctive ones
flags.DEFINE_boolean('gen_more_and_select', False, '')
flags.DEFINE_integer('gen_how_many', 2000, '')

FLAGS = flags.FLAGS

pp = pprint.PrettyPrinter()


def main(_):

    pp.pprint(flags.FLAGS.__flags)

    assert FLAGS.mode == 'wgan-gp'

    if FLAGS.dataset == 'cifar-10':
        import cifar10
        NUM_CLASSES = 10  # 类别数量
        NUM_TRAIN_SAMPLES_PER_CLASS = 5000  # 每个类别的训练样本个数
        NUM_TEST_SAMPLES_PER_CLASS = 1000  # 每个类别的训练样本个数
        train_images, train_labels, train_one_hot_labels, \
            test_images, test_labels, test_one_hot_labels, \
            raw_images_train, raw_images_test, pixel_mean = cifar10.load_data(mean_subtraction=True)
    elif FLAGS.dataset == 'cifar-100':
        import cifar100
        NUM_CLASSES = 100  # 类别数量
        NUM_TRAIN_SAMPLES_PER_CLASS = 500  # 每个类别的训练样本个数
        NUM_TEST_SAMPLES_PER_CLASS = 100  # 每个类别的训练样本个数
        train_images, train_labels, train_one_hot_labels, \
            test_images, test_labels, test_one_hot_labels, \
            raw_images_train, raw_images_test, pixel_mean = cifar100.load_data(mean_subtraction=True)

    # 训练样本总数
    NUM_TRAIN_SAMPLES_TOTAL = NUM_CLASSES * NUM_TRAIN_SAMPLES_PER_CLASS
    NUM_TEST_SAMPLES_TOTAL = NUM_CLASSES * NUM_TEST_SAMPLES_PER_CLASS

    def build_cnn(inputs, is_training):
        train_or_test = {True: 'train', False: 'test'}
        if FLAGS.network_arch == 'lenet':
            logits, end_points = utils_lenet.lenet(inputs, num_classes=NUM_CLASSES, is_training=is_training,
                                                   use_dropout=FLAGS.use_dropout,
                                                   scope=('LeNet-'+train_or_test[is_training]))
        elif FLAGS.network_arch == 'resnet':
            logits, end_points = utils_resnet.ResNet(inputs, train_or_test[is_training], num_outputs=NUM_CLASSES,
                                                     alpha=0.0, n=FLAGS.num_resblocks,
                                                     scope=('ResNet-'+train_or_test[is_training]))
        elif FLAGS.network_arch == 'nin':
            logits, end_points = utils_nin.nin(inputs, is_training=is_training, num_classes=NUM_CLASSES,
                                               scope=('NIN-' + train_or_test[is_training]))
        else:
            raise Exception()
        return logits, end_points

    '''
    Define variables
    '''
    if not FLAGS.only_gen_no_cls:

        # save all intermediate result in the result_folder
        method_name = '_'.join(os.path.basename(__file__).split('.')[0].split('_')[2:])
        method_name += '_gen_%d_and_select' % FLAGS.gen_how_many if FLAGS.gen_more_and_select else ''
        method_name += '_auto-%.1f-%.1f' % (FLAGS.auto_param1, FLAGS.auto_param2) \
            if FLAGS.auto_choose_num_exemplars else ('_%d' % FLAGS.num_exemplars_per_class if not FLAGS.memory_constrained else '')
        method_name += '_%s' % FLAGS.exemplar_select_criterion
        method_name += '_%.1f-%.1f' % (FLAGS.proto_weight, FLAGS.gen_weight)
        method_name += '_cache_%d' % FLAGS.cache_size_per_class if FLAGS.use_cache_for_gen_samples else ''
        method_name += '_icarl_%d' % FLAGS.memory_upperbound if FLAGS.memory_constrained else ''
        cls_func = '' if FLAGS.use_softmax else '_sigmoid'
        result_folder = os.path.join(FLAGS.result_dir, FLAGS.dataset,
                                     FLAGS.network_arch + ('_%d' % FLAGS.num_resblocks if FLAGS.network_arch == 'resnet' else '') + cls_func + '_init_' + FLAGS.init_strategy,
                                     'weight_decay_' + str(FLAGS.weight_decay),
                                     'base_lr_' + str(FLAGS.base_lr),
                                     'adam_lr_' + str(FLAGS.adam_lr))
        if FLAGS.gan_finetune and 'gan' in method_name:
            result_folder = os.path.join(result_folder,
                                         method_name + '_finetune_' + FLAGS.pretrained_model_sub_dir.replace('/', '_'))
        else:
            result_folder = os.path.join(result_folder,
                                         method_name)

        if os.path.exists(result_folder):
            temp_i = 2
            while True:
                result_folder_mod = result_folder + '_run-' + str(temp_i)
                if not os.path.exists(result_folder_mod):
                    result_folder = result_folder_mod
                    break
                temp_i += 1
        os.makedirs(result_folder)

        graph_cls = tf.Graph()
        with graph_cls.as_default():
            batch_images = tf.placeholder(tf.float32, shape=[None, 32, 32, 3])
            batch = tf.Variable(0, trainable=False, name='LeNet-train/iteration')
            learning_rate = tf.placeholder(tf.float32, shape=[])

            '''
            Network output mask
            '''
            mask_output = tf.placeholder(tf.bool, shape=[NUM_CLASSES])

            '''
            Old and new ground truth
            '''
            one_hot_labels_truncated = tf.placeholder(tf.float32, shape=[None, None])

            '''
            Define the training network
            '''
            train_logits, _ = build_cnn(batch_images, True)
            train_masked_logits = tf.gather(train_logits, tf.squeeze(tf.where(mask_output)), axis=1)    # masking operation
            train_masked_logits = tf.cond(tf.equal(tf.rank(train_masked_logits), 1),
                                          lambda: tf.expand_dims(train_masked_logits, 1),
                                          lambda: train_masked_logits)    # convert to (N, 1) if the shape is (N,), otherwise softmax would output wrong values
            # Train accuracy(since there is only one class excluding the old recorded responses, this accuracy is not very meaningful)
            train_pred = tf.argmax(train_masked_logits, 1)
            train_ground_truth = tf.argmax(one_hot_labels_truncated, 1)
            correct_prediction = tf.equal(train_pred, train_ground_truth)
            train_accuracy = tf.reduce_mean(tf.cast(correct_prediction, tf.float32))
            train_batch_weights = tf.placeholder(tf.float32, shape=[None])

            reg_weights = tf.get_collection(tf.GraphKeys.REGULARIZATION_LOSSES)
            regularization_loss = FLAGS.weight_decay * tf.add_n(reg_weights)

            '''
            More Settings
            '''
            if FLAGS.use_softmax:
                empirical_loss = tf.losses.softmax_cross_entropy(onehot_labels=one_hot_labels_truncated,
                                                                 logits=train_masked_logits,
                                                                 weights=train_batch_weights)
            else:
                empirical_loss = tf.losses.sigmoid_cross_entropy(multi_class_labels=one_hot_labels_truncated,
                                                                 logits=train_masked_logits,
                                                                 weights=train_batch_weights)

            loss = empirical_loss + regularization_loss
            if FLAGS.use_momentum:
                opt = tf.train.MomentumOptimizer(learning_rate, FLAGS.momentum).minimize(loss, global_step=batch)
            else:
                opt = tf.train.GradientDescentOptimizer(learning_rate).minimize(loss, global_step=batch)

            '''
            Define the testing network
            '''
            test_logits, _ = build_cnn(batch_images, False)
            test_masked_logits = tf.gather(test_logits, tf.squeeze(tf.where(mask_output)), axis=1)
            test_masked_logits = tf.cond(tf.equal(tf.rank(test_masked_logits), 1),
                                         lambda: tf.expand_dims(test_masked_logits, 1),
                                         lambda: test_masked_logits)
            test_masked_prob = tf.nn.softmax(test_masked_logits)
            test_pred = tf.argmax(test_masked_logits, 1)
            test_accuracy = tf.placeholder(tf.float32)

            '''
            Copy network
            '''
            # test_network的参数与train_network相同
            if FLAGS.network_arch == 'resnet':
                all_variables = tf.get_collection(tf.GraphKeys.WEIGHTS)
            else:
                all_variables = tf.trainable_variables()
            copy_ops = [all_variables[ix + len(all_variables) // 2].assign(var.value()) for ix, var in
                        enumerate(all_variables[0:len(all_variables) // 2])]

            '''
            Init certain layers when new classes added
            '''
            init_ops = tf.no_op()
            if FLAGS.init_strategy == 'all':
                init_ops = tf.global_variables_initializer()
            elif FLAGS.init_strategy == 'last':
                if FLAGS.network_arch == 'lenet':
                    init_vars = [var for var in tf.global_variables() if 'fc4' in var.name and 'train' in var.name]
                elif FLAGS.network_arch == 'resnet':
                    init_vars = [var for var in tf.global_variables() if 'fc' in var.name and 'train' in var.name]
                elif FLAGS.network_arch == 'nin':
                    init_vars = [var for var in tf.global_variables() if 'ccp6' in var.name and 'train' in var.name]
                init_ops = tf.initialize_variables(init_vars)

            '''
            Create session
            '''
            config = tf.ConfigProto()
            config.gpu_options.allow_growth = True
            sess = tf.Session(config=config, graph=graph_cls)
            sess.run(tf.global_variables_initializer())  # 初始化

            saver = tf.train.Saver()

        '''
        Summary
        '''
        train_loss_summary = tf.summary.scalar('train_loss', loss)
        train_acc_summary = tf.summary.scalar('train_accuracy', train_accuracy)
        test_acc_summary = tf.summary.scalar('test_accuracy', test_accuracy)

        summary_dir = os.path.join(result_folder, 'summary')
        if not os.path.exists(summary_dir):
            os.makedirs(summary_dir)
        train_summary_writer = tf.summary.FileWriter(os.path.join(summary_dir, 'train'), sess.graph)
        test_summary_writer = tf.summary.FileWriter(os.path.join(summary_dir, 'test'))

        iteration = 0

        '''
        Declaration of other vars
        '''
        # 累积准确率
        aver_acc_over_time = []
        aver_acc_per_class_over_time = []

        # 网络的mask
        mask_output_val = np.zeros([NUM_CLASSES], dtype=bool)

        '''
        Cache(accelerate)
        '''
        cache_dir = os.path.join(result_folder, 'cache')
        if not os.path.exists(cache_dir):
            os.makedirs(cache_dir)

        '''
        Exemplars(for ablation study and other purposes)
        '''
        exemplars_dir = os.path.join(result_folder, 'exemplars')
        if not os.path.exists(exemplars_dir):
            os.makedirs(exemplars_dir)

    '''
    Train generative model(DC-GAN)
    '''
    run_config = tf.ConfigProto()
    run_config.gpu_options.allow_growth = True
    graph_gen = tf.Graph()
    sess_wgan = tf.Session(config=run_config, graph=graph_gen)

    wgan_obj = GAN(sess_wgan, graph_gen,
                   dataset_name=FLAGS.dataset,
                   mode=FLAGS.mode,
                   batch_size=FLAGS.batch_size,
                   dim=FLAGS.dim,
                   output_dim=FLAGS.output_dim,
                   lambda_param=FLAGS.lambda_param,
                   critic_iters=FLAGS.critic_iters,
                   iters=FLAGS.iters,
                   result_dir=FLAGS.result_dir_wgan,
                   checkpoint_interval=FLAGS.gan_save_interval,
                   adam_lr=FLAGS.adam_lr,
                   adam_beta1=FLAGS.adam_beta1,
                   adam_beta2=FLAGS.adam_beta2,
                   finetune=FLAGS.gan_finetune,
                   finetune_from=FLAGS.gan_finetune_from,
                   pretrained_model_base_dir=FLAGS.pretrained_model_base_dir,
                   pretrained_model_sub_dir=FLAGS.pretrained_model_sub_dir)

    exemplars = []

    '''
    Class Incremental Learning
    '''
    print('Starting from category ' + str(FLAGS.from_class_idx + 1) + ' to ' + str(FLAGS.to_class_idx + 1))
    for category_idx in range(FLAGS.from_class_idx, FLAGS.to_class_idx + 1):

        print('Adding Category ' + str(category_idx + 1))

        # Training set(current category)
        train_indices = [idx for idx in range(NUM_TRAIN_SAMPLES_TOTAL) if train_labels[idx] == category_idx]
        test_indices_cur_cls = [idx for idx in range(NUM_TEST_SAMPLES_TOTAL) if test_labels[idx] == category_idx]

        train_x_cur_cls = raw_images_train[train_indices, :]
        test_x_cur_cls = raw_images_test[test_indices_cur_cls, :]
        # Record the response of the new data using the old model(category_idx is consistent with the number of True in mask_output_val_prev)
        train_y_truncated = train_one_hot_labels[train_indices, :category_idx+1]

        '''
        Train generative model(W-GAN)
        '''
        if wgan_obj.check_model(category_idx):
            print(" [*] Model of Class %d exists. Skip the training process" % (category_idx + 1))
        else:
            print(" [*] Model of Class %d does not exist. Start the training process" % (category_idx + 1))
            wgan_obj.train(train_x_cur_cls, test_x_cur_cls, category_idx)

        '''
        Train classification model
        '''
        # 第一个类别不用训练classifier
        if not FLAGS.only_gen_no_cls:

            mask_output_val[:category_idx+1] = True

            if category_idx > 0:

                # init certain layers
                sess.run(init_ops)

                '''
                Generate samples of old classes
                '''
                train_x = np.copy(train_x_cur_cls)
                train_weights_val = np.ones(len(train_x_cur_cls))
                for old_category_idx in range(0, category_idx):

                    # Load old class model
                    num_gen_samples_x_needed = NUM_TRAIN_SAMPLES_PER_CLASS - len(exemplars[old_category_idx])
                    if num_gen_samples_x_needed > 0:

                        # if FLAGS.use_cache_for_gen_samples:
                        #     cache_file = os.path.join(cache_dir, 'class_%d.npy' % (old_category_idx + 1))
                        #     if os.path.exists(cache_file):
                        #         gen_samples_x = np.load(cache_file)
                        #     else:
                        #         if not wgan_obj.load(old_category_idx)[0]:
                        #             raise Exception("[!] Train a model first, then run test mode")
                        #         gen_samples_x, _, _ = wgan_obj.test(FLAGS.cache_size_per_class)
                        #         np.save(cache_file, gen_samples_x)
                        #
                        #     gen_samples_x_idx = np.random.choice(len(gen_samples_x),
                        #                                          num_gen_samples_x_needed,
                        #                                          replace=False)
                        #     gen_samples_x = gen_samples_x[gen_samples_x_idx]
                        # else:
                        #     if not wgan_obj.load(old_category_idx)[0]:
                        #         raise Exception("[!] Train a model first, then run test mode")
                        #     gen_samples_x, _, _ = wgan_obj.test(num_gen_samples_x_needed)


                        # import wgan.tflib.save_images
                        # wgan.tflib.save_images.save_images(gen_samples_x[:128].reshape((128, 3, 32, 32)),
                        #                                    'test.jpg')
                        train_x = np.concatenate((train_x, gen_samples_x, exemplars[old_category_idx]))
                        train_weights_val = np.concatenate((train_weights_val,
                                                            np.ones(len(gen_samples_x)) * FLAGS.gen_weight,
                                                            np.ones(len(exemplars[old_category_idx])) * FLAGS.proto_weight))
                    elif num_gen_samples_x_needed == 0:
                        train_x = np.concatenate((train_x, exemplars[old_category_idx]))
                        train_weights_val = np.concatenate((train_weights_val,
                                                            np.ones(len(exemplars[old_category_idx])) * FLAGS.proto_weight))
                    gen_samples_y = np.zeros((NUM_TRAIN_SAMPLES_PER_CLASS, category_idx+1))
                    gen_samples_y[:, old_category_idx] = np.ones((NUM_TRAIN_SAMPLES_PER_CLASS))
                    train_y_truncated = np.concatenate((train_y_truncated, gen_samples_y))

                # # DEBUG:
                # train_indices = [idx for idx in range(NUM_SAMPLES_TOTAL) if train_labels[idx] <= category_idx]
                # train_x = raw_images_train[train_indices, :]
                # # Record the response of the new data using the old model(category_idx is consistent with the number of True in mask_output_val_prev)
                # train_y_truncated = train_one_hot_labels[train_indices, :category_idx + 1]

                # Training set
                # Convert the raw images from the data-files to floating-points.
                train_x = cifar100.convert_images(train_x, pixel_mean=pixel_mean)

                # Testing set
                test_indices = [idx for idx in range(len(test_labels)) if test_labels[idx] <= category_idx]
                test_x = test_images[test_indices]
                test_y = test_labels[test_indices]

                # Shuffle the indices and create mini-batch
                batch_indices_perm = []

                epoch_idx = 0
                lr = FLAGS.base_lr

                '''
                Training with mixed data
                '''
                while True:
                    # Generate mini-batch
                    if len(batch_indices_perm) == 0:
                        if epoch_idx >= FLAGS.epochs_per_category:
                            break
                        if epoch_idx in lr_strat:
                            lr /= FLAGS.lr_factor
                            print("NEW LEARNING RATE: %f" % lr)
                        epoch_idx = epoch_idx + 1

                        # print('Epoch %d' % epoch_idx)

                        shuffled_indices = range(train_x.shape[0])
                        np.random.shuffle(shuffled_indices)
                        for i in range(0, len(shuffled_indices), FLAGS.train_batch_size):
                            batch_indices_perm.append(shuffled_indices[i:i + FLAGS.train_batch_size])
                        batch_indices_perm.reverse()

                    popped_batch_idx = batch_indices_perm.pop()

                    # Use the random index to select random images and labels.
                    train_weights_batch_val = train_weights_val[popped_batch_idx]
                    train_x_batch = train_x[popped_batch_idx, :, :, :]
                    train_y_batch = [train_y_truncated[k] for k in popped_batch_idx]

                    # Train
                    train_loss_summary_str, train_acc_summary_str, train_accuracy_val, \
                        train_loss_val, train_empirical_loss_val, train_reg_loss_val, _ = sess.run(
                            [train_loss_summary, train_acc_summary, train_accuracy, loss, empirical_loss,
                                regularization_loss, opt], feed_dict={batch_images: train_x_batch,
                                                                      one_hot_labels_truncated: train_y_batch,
                                                                      mask_output: mask_output_val,
                                                                      learning_rate: lr,
                                                                      train_batch_weights: train_weights_batch_val})

                    # Test
                    if iteration % FLAGS.test_interval == 0:
                        sess.run(copy_ops)  # 拷贝LeNet-train中的weights/biases到LeNet-test上
                        # devide and conquer: to avoid allocating too much GPU memory
                        test_pred_val = []
                        for i in range(0, len(test_x), FLAGS.test_batch_size):
                            test_x_batch = test_x[i:i + FLAGS.test_batch_size]
                            test_pred_val_batch = sess.run(test_pred, feed_dict={batch_images: test_x_batch,
                                                                                 mask_output: mask_output_val})
                            test_pred_val.extend(test_pred_val_batch)

                        test_accuracy_val = 1. * np.sum(np.equal(test_pred_val, test_y)) / (len(test_pred_val))
                        test_per_class_accuracy_val = np.diag(confusion_matrix(test_y, test_pred_val))

                        test_acc_summary_str = sess.run(test_acc_summary, feed_dict={test_accuracy: test_accuracy_val})

                        test_summary_writer.add_summary(test_acc_summary_str, iteration)

                        print("TEST: step %d, lr %.4f, accuracy %g" % (iteration, lr, test_accuracy_val))
                        print("PER CLASS ACCURACY: " + " | ".join(str(o) + '%' for o in test_per_class_accuracy_val))

                    # Print the training logs
                    if iteration % FLAGS.display_interval == 0:
                        train_summary_writer.add_summary(train_loss_summary_str, iteration)
                        train_summary_writer.add_summary(train_acc_summary_str, iteration)
                        print("TRAIN: epoch %d, step %d, lr %.4f, accuracy %g, loss %g, empirical %g, reg %g" % (
                            epoch_idx, iteration, lr, train_accuracy_val, train_loss_val,
                            train_empirical_loss_val, train_reg_loss_val))

                    iteration = iteration + 1

                '''
                Final test(before the next class is added)
                '''
                sess.run(copy_ops)  # 拷贝LeNet-train中的weights/biases到LeNet-test上
                # devide and conquer: to avoid allocating too much GPU memory
                test_pred_val = []
                for i in range(0, len(test_x), FLAGS.test_batch_size):
                    test_x_batch = test_x[i:i + FLAGS.test_batch_size]
                    test_pred_val_batch = sess.run(test_pred, feed_dict={batch_images: test_x_batch,
                                                                         mask_output: mask_output_val})
                    test_pred_val.extend(test_pred_val_batch)

                test_accuracy_val = 1. * np.sum(np.equal(test_pred_val, test_y)) / (len(test_pred_val))
                test_per_class_accuracy_val = np.diag(confusion_matrix(test_y, test_pred_val))

                # Record and save the cumulative accuracy
                aver_acc_over_time.append(test_accuracy_val)
                aver_acc_per_class_over_time.append(test_per_class_accuracy_val)
                np_file_result = os.path.join(result_folder, 'acc_over_time')
                np.savez(np_file_result, flags=flags.FLAGS.__flags, aver_acc_over_time=aver_acc_over_time,
                         aver_acc_per_class_over_time=aver_acc_per_class_over_time)
                visualize_result.vis(np_file_result)

            # select the exemplars
            train_x_cur_cls_normalized = cifar100.convert_images(train_x_cur_cls, pixel_mean=pixel_mean)
            sess.run(copy_ops)  # 拷贝LeNet-train中的weights/biases到LeNet-test上
            # devide and conquer: to avoid allocating too much GPU memory
            train_prob_cur_cls_val = sess.run(test_masked_prob, feed_dict={batch_images: train_x_cur_cls_normalized,
                                                                           mask_output: mask_output_val})
            train_prob_cur_cls_val = train_prob_cur_cls_val[:, category_idx]

            # use iCaRL-like memory mechanism to save exemplars or not
            if FLAGS.memory_constrained:

                if FLAGS.auto_choose_num_exemplars:  # auto or fixed number of exemplars
                    # check if we can save all new samples as exemplars
                    if NUM_TRAIN_SAMPLES_PER_CLASS > FLAGS.memory_upperbound - sum([len(exemplars[i]) for i in range(len(exemplars))]):
                        # load inception scores of all classes
                        save_exemplars_ratios = []
                        for i in range(category_idx + 1):
                            inception_score = wgan_obj.load_inception_score(i)
                            save_exemplars_ratio = FLAGS.auto_param1 - FLAGS.auto_param2 * inception_score
                            save_exemplars_ratios.append(save_exemplars_ratio)

                        save_exemplars_ratios = np.array(save_exemplars_ratios)
                        keep_exemplars_num = np.floor(save_exemplars_ratios * FLAGS.memory_upperbound
                                                         / sum(save_exemplars_ratios)).astype(int)
                        for old_category_idx in range(category_idx):
                            exemplars[old_category_idx] = exemplars[old_category_idx][:keep_exemplars_num[old_category_idx]]
                        num_exemplars_cur_cls = keep_exemplars_num[-1]
                    else:
                        num_exemplars_cur_cls = NUM_TRAIN_SAMPLES_PER_CLASS

                else:
                    num_exemplars_per_cls = int(FLAGS.memory_upperbound // (category_idx + 1))
                    num_exemplars_per_cls = min(num_exemplars_per_cls, NUM_TRAIN_SAMPLES_PER_CLASS)
                    # remove redundant elements in the memory for previous classes
                    if category_idx > 0 and len(exemplars[0]) > num_exemplars_per_cls:
                        for old_category_idx in range(category_idx):
                            exemplars[old_category_idx] = exemplars[old_category_idx][:num_exemplars_per_cls]

                    # add how many new elements in the memory for the current class
                    num_exemplars_cur_cls = num_exemplars_per_cls
                    print(' [*] Store %d exemplars for each class' % num_exemplars_cur_cls)

            else:
                if FLAGS.auto_choose_num_exemplars: # auto or fixed number of exemplars
                    inception_score = wgan_obj.load_inception_score(category_idx)
                    num_exemplars_cur_cls = int(np.floor(FLAGS.auto_param1 - FLAGS.auto_param2 * inception_score))
                    print(' [*] Inception score %f, store %d exemplars' % (inception_score, num_exemplars_cur_cls))
                else:
                    num_exemplars_cur_cls = FLAGS.num_exemplars_per_class

            selected_indices = np.array(range(len(train_prob_cur_cls_val)))
            if FLAGS.exemplar_select_criterion == 'high':
                selected_indices = train_prob_cur_cls_val.argsort()[:-(num_exemplars_cur_cls+1):-1]    # select the last 20
            elif FLAGS.exemplar_select_criterion == 'low':
                selected_indices = train_prob_cur_cls_val.argsort()[:num_exemplars_cur_cls]    # select the last 20
            elif FLAGS.exemplar_select_criterion == 'random':
                random_idx = range(len(train_prob_cur_cls_val))
                np.random.shuffle(random_idx)
                selected_indices = random_idx[:num_exemplars_cur_cls]

            exemplars.append(train_x_cur_cls[selected_indices])

            np_file_exemplars = os.path.join(exemplars_dir, 'exemplars_%d' % (category_idx+1))
            np.save(np_file_exemplars, exemplars)

    # 保存最终的模型
    if not FLAGS.only_gen_no_cls:
        checkpoint_dir = os.path.join(result_folder, 'checkpoints')
        if not os.path.exists(checkpoint_dir):
            os.makedirs(checkpoint_dir)
        saver.save(sess, os.path.join(checkpoint_dir, 'model.ckpt'))
        sess.close()


if __name__ == '__main__':
    tf.app.run()
