# -*- coding:utf-8 -*-

'''
Implememtation of the proposed method ESGR-reals (for ablation study only)
It needs the exemplars of ESGR-mix
'''

import tensorflow as tf
tf.set_random_seed(1993)

import utils_resnet_64x64

import numpy as np
np.random.seed(1993)

import os
import pprint
import visualize_result

from sklearn.metrics import confusion_matrix

import pickle

from wgan.model_64x64_conditional import WGAN64x64

import imagenet_64x64

flags = tf.app.flags

flags.DEFINE_float('adam_lr', 2e-4, 'default: 2e-4')
flags.DEFINE_integer("output_dim", 64*64*3, "Number of pixels in imagenet_64x64")

flags.DEFINE_string("dataset", "imagenet_64x64_dogs", "The name of dataset")

flags.DEFINE_boolean('use_momentum', True, 'Gradient descent or gradient descent with momentum')
flags.DEFINE_float('momentum', 0.9, '')

flags.DEFINE_integer('epochs_per_category', 60, 'number of epochs for each training session')
flags.DEFINE_integer('train_batch_size', 128, 'training batch size')
flags.DEFINE_integer('test_batch_size', 128, 'test batch size')

flags.DEFINE_float('base_lr', .2, '2. for sigmoid, .2 for softmax')
flags.DEFINE_float('weight_decay', 0.00001, '0.00001')
flags.DEFINE_float('lr_factor', 5., '')
flags.DEFINE_integer('display_interval', 20, '')
flags.DEFINE_integer('test_interval', 100, '')
lr_strat = [20, 30, 40, 50]

flags.DEFINE_string('result_dir', 'result/', '')

# Network architecture
flags.DEFINE_string('network_arch', 'resnet', 'resnet')
# flags.DEFINE_integer('num_resblocks', 5, 'number of resblocks when ResNet is used')
flags.DEFINE_boolean('use_softmax', True, 'True: softmax; False: sigmoid')
flags.DEFINE_boolean('no_truncate', False, '')

# Add how many classes every time
flags.DEFINE_integer('nb_cl', 10, '')

# DEBUG
flags.DEFINE_integer('from_class_idx', 0, 'starting category_idx')
flags.DEFINE_integer('to_class_idx', 119, 'ending category_idx')

# Init params when new nodes added
flags.DEFINE_string('init_strategy', 'no', 'no | last | all')

# Order file
flags.DEFINE_string('order_file', 'order_1', '')

# Exemplar parent folder(indicate that which method is used)
flags.DEFINE_string('exemplars_base_folder', 'esgr_mix_high_1.0-1.0_icarl_2400_smoothing_1.0', '')

# Data aug
flags.DEFINE_boolean('flip', False, '')

FLAGS = flags.FLAGS

pp = pprint.PrettyPrinter()


def main(_):

    pp.pprint(flags.FLAGS.__flags)

    order = []
    with open('imagenet_64x64_dogs_%s.txt' % FLAGS.order_file) as file_in:
        for line in file_in.readlines():
            order.append(int(line))
    order = np.array(order)

    NUM_CLASSES = 120
    NUM_TEST_SAMPLES_PER_CLASS = 50
    NUM_TRAIN_SAMPLES_PER_CLASS = 1300 # around 1300

    def build_cnn(inputs, is_training):
        train_or_test = {True: 'train', False: 'test'}
        if FLAGS.network_arch == 'resnet':
            logits, end_points = utils_resnet_64x64.ResNet(inputs, train_or_test[is_training],
                                                           num_outputs=NUM_CLASSES,
                                                           alpha=0.0,
                                                           scope=('ResNet-' + train_or_test[is_training]))
        else:
            raise Exception()
        return logits, end_points

    # Save all intermediate result in the result_folder
    method_name = '_'.join(os.path.basename(__file__).split('.')[0].split('_')[4:])

    cls_func = '' if FLAGS.use_softmax else '_sigmoid'
    result_base_folder = os.path.join(FLAGS.result_dir, FLAGS.dataset + ('_flip' if FLAGS.flip else '') + '_' + FLAGS.order_file,
                                      'nb_cl_' + str(FLAGS.nb_cl),
                                      'non_truncated' if FLAGS.no_truncate else 'truncated',
                                      FLAGS.network_arch + cls_func + '_init_' + FLAGS.init_strategy,
                                      'weight_decay_' + str(FLAGS.weight_decay),
                                      'base_lr_' + str(FLAGS.base_lr),
                                      'adam_lr_' + str(FLAGS.adam_lr))

    result_folder = os.path.join(result_base_folder,
                                 FLAGS.exemplars_base_folder + '_ablation_epoch_based')

    exemplars_folder = os.path.join(result_base_folder,
                                    FLAGS.exemplars_base_folder,
                                    'exemplars')

    if not os.path.exists(exemplars_folder):
        raise Exception()

    # Add a "_run-i" suffix to the folder name if the folder exists
    if os.path.exists(result_folder):
        temp_i = 2
        while True:
            result_folder_mod = result_folder + '_run-' + str(temp_i)
            if not os.path.exists(result_folder_mod):
                result_folder = result_folder_mod
                break
            temp_i += 1
    os.makedirs(result_folder)
    print('Result folder: %s' % result_folder)

    graph_cls = tf.Graph()
    with graph_cls.as_default():
        '''
        Define variables
        '''
        batch_images = tf.placeholder(tf.float32, shape=[None, 64, 64, 3])
        batch = tf.Variable(0, trainable=False)
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
        train_masked_logits = tf.gather(train_logits, tf.squeeze(tf.where(mask_output)),
                                        axis=1)  # masking operation
        train_masked_logits = tf.cond(tf.equal(tf.rank(train_masked_logits), 1),
                                      lambda: tf.expand_dims(train_masked_logits, 1),
                                      lambda: train_masked_logits)  # convert to (N, 1) if the shape is (N,), otherwise softmax would output wrong values
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
        Copy network (define the copying op)
        '''
        if FLAGS.network_arch == 'resnet':
            all_variables = tf.get_collection(tf.GraphKeys.WEIGHTS)
        else:
            raise Exception('Invalid network architecture')
        copy_ops = [all_variables[ix + len(all_variables) // 2].assign(var.value()) for ix, var in
                    enumerate(all_variables[0:len(all_variables) // 2])]

        '''
        Init certain layers when new classes added
        '''
        init_ops = tf.no_op()
        if FLAGS.init_strategy == 'all':
            init_ops = tf.global_variables_initializer()
        elif FLAGS.init_strategy == 'last':
            if FLAGS.network_arch == 'resnet':
                init_vars = [var for var in tf.global_variables() if 'fc' in var.name and 'train' in var.name]
            init_ops = tf.initialize_variables(init_vars)

        '''
        Create session
        '''
        config = tf.ConfigProto()
        config.gpu_options.allow_growth = True
        sess = tf.Session(config=config, graph=graph_cls)
        sess.run(tf.global_variables_initializer())

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
    # Average accuracy on seen classes
    aver_acc_over_time = dict()
    aver_acc_per_class_over_time = dict()
    conf_mat_over_time = dict()

    # Network mask
    mask_output_val = np.zeros([NUM_CLASSES], dtype=bool)
    mask_output_test = np.zeros([NUM_CLASSES], dtype=bool)

    test_images, test_labels, test_one_hot_labels, raw_images_test = imagenet_64x64.load_test_data()

    test_x = np.zeros([0, 64, 64, 3], dtype=np.float32)
    test_y = np.zeros([0], dtype=np.float32)

    '''
    Class Incremental Learning
    '''
    print('Starting from category ' + str(FLAGS.from_class_idx + 1) + ' to ' + str(FLAGS.to_class_idx + 1))
    print('Adding %d categories every time' % FLAGS.nb_cl)
    assert(FLAGS.from_class_idx % FLAGS.nb_cl == 0)
    for category_idx in range(FLAGS.from_class_idx, FLAGS.to_class_idx + 1, FLAGS.nb_cl):

        to_category_idx = category_idx + FLAGS.nb_cl - 1
        if FLAGS.nb_cl == 1:
            print('Adding Category ' + str(category_idx + 1))
        else:
            print('Adding Category %d-%d' % (category_idx + 1, to_category_idx + 1))

        train_x_gan = np.zeros([0, FLAGS.output_dim], dtype=np.uint8)
        train_y_one_hot = np.zeros([0, NUM_CLASSES], dtype=np.float32)

        for category_idx_in_group in range(category_idx, to_category_idx + 1):
            real_category_idx = order[category_idx_in_group]
            _, raw_images_train_cur_cls = imagenet_64x64.load_train_data(real_category_idx, flip=FLAGS.flip)

            train_x_gan = np.concatenate((train_x_gan, raw_images_train_cur_cls))

            train_y_one_hot_cur_cls = np.zeros([len(raw_images_train_cur_cls), NUM_CLASSES])
            train_y_one_hot_cur_cls[:, category_idx_in_group] = np.ones(len(raw_images_train_cur_cls))

            test_indices_cur_cls = [idx for idx in range(len(test_labels)) if
                                    test_labels[idx] == real_category_idx]
            test_x_cur_cls = test_images[test_indices_cur_cls, :]
            test_y_cur_cls = np.ones([len(test_indices_cur_cls)]) * category_idx_in_group

            test_x = np.concatenate((test_x, test_x_cur_cls))
            test_y = np.concatenate((test_y, test_y_cur_cls))
            train_y_one_hot = np.concatenate((train_y_one_hot, train_y_one_hot_cur_cls))


        '''
        Train classification model
        '''
        # No need to train the classifier if there is only one class
        if to_category_idx > 0 or not FLAGS.use_softmax:

            # init certain layers
            sess.run(init_ops)

            if FLAGS.no_truncate:
                mask_output_val[:] = True
            else:
                mask_output_val[:to_category_idx + 1] = True

            # Test on all seen classes
            mask_output_test[:to_category_idx + 1] = True

            '''
            Generate samples of old classes
            '''
            train_x = np.copy(train_x_gan)
            if FLAGS.no_truncate:
                train_y_truncated = train_y_one_hot[:, :]
            else:
                train_y_truncated = train_y_one_hot[:, :to_category_idx + 1]
            train_weights_val = np.ones(len(train_x))

            if category_idx > 0:
                exemplars = np.load(os.path.join(exemplars_folder, 'exemplars_%d.npy' % category_idx))
                for old_category_idx in range(0, category_idx):

                    # Load old class model
                    exemplars_idx_cur_cls = np.random.choice(len(exemplars[old_category_idx]),
                                                             NUM_TRAIN_SAMPLES_PER_CLASS, replace=True)
                    exemplars_cur_cls = exemplars[old_category_idx][exemplars_idx_cur_cls]

                    train_x = np.concatenate((train_x, exemplars_cur_cls))
                    train_weights_val = np.concatenate((train_weights_val,
                                                        np.ones(NUM_TRAIN_SAMPLES_PER_CLASS)))

                    train_y_old_cls = np.zeros((NUM_TRAIN_SAMPLES_PER_CLASS, to_category_idx + 1))
                    train_y_old_cls[:, old_category_idx] = np.ones((NUM_TRAIN_SAMPLES_PER_CLASS))
                    train_y_truncated = np.concatenate((train_y_truncated, train_y_old_cls))

            # # DEBUG:
            # train_indices = [idx for idx in range(NUM_SAMPLES_TOTAL) if train_labels[idx] <= category_idx]
            # train_x = raw_images_train[train_indices, :]
            # # Record the response of the new data using the old model(category_idx is consistent with the number of True in mask_output_val_prev)
            # train_y_truncated = train_one_hot_labels[train_indices, :category_idx + 1]

            # Training set
            # Convert the raw images from the data-files to floating-points.
            train_x = imagenet_64x64.convert_images(train_x)

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
                    sess.run(copy_ops)

                    # Divide and conquer: to avoid allocating too much GPU memory
                    test_pred_val = []
                    for i in range(0, len(test_x), FLAGS.test_batch_size):
                        test_x_batch = test_x[i:i + FLAGS.test_batch_size]
                        test_pred_val_batch = sess.run(test_pred, feed_dict={batch_images: test_x_batch,
                                                                             mask_output: mask_output_test})
                        test_pred_val.extend(test_pred_val_batch)

                    test_accuracy_val = 1. * np.sum(np.equal(test_pred_val, test_y)) / (len(test_pred_val))
                    test_per_class_accuracy_val = np.diag(confusion_matrix(test_y, test_pred_val)) * 2
                    # I simply multiply the correct predictions by 2 to calculate the accuracy since there are 50 samples per class in the test set

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
            sess.run(copy_ops)
            # Divide and conquer: to avoid allocating too much GPU memory
            test_pred_val = []
            for i in range(0, len(test_x), FLAGS.test_batch_size):
                test_x_batch = test_x[i:i + FLAGS.test_batch_size]
                test_pred_val_batch = sess.run(test_pred, feed_dict={batch_images: test_x_batch,
                                                                     mask_output: mask_output_test})
                test_pred_val.extend(test_pred_val_batch)

            test_accuracy_val = 1. * np.sum(np.equal(test_pred_val, test_y)) / (len(test_pred_val))
            conf_mat = confusion_matrix(test_y, test_pred_val)
            test_per_class_accuracy_val = np.diag(conf_mat)

            # Record and save the cumulative accuracy
            aver_acc_over_time[to_category_idx] = test_accuracy_val
            aver_acc_per_class_over_time[to_category_idx] = test_per_class_accuracy_val
            conf_mat_over_time[to_category_idx] = conf_mat

            dump_obj = dict()
            dump_obj['flags'] = flags.FLAGS.__flags
            dump_obj['aver_acc_over_time'] = aver_acc_over_time
            dump_obj['aver_acc_per_class_over_time'] = aver_acc_per_class_over_time
            dump_obj['conf_mat_over_time'] = conf_mat_over_time

            np_file_result = os.path.join(result_folder, 'acc_over_time.pkl')
            with open(np_file_result, 'wb') as file:
                pickle.dump(dump_obj, file)

            visualize_result.vis(np_file_result, 'ImageNetDogs')

    # Save the final model
    checkpoint_dir = os.path.join(result_folder, 'checkpoints')
    if not os.path.exists(checkpoint_dir):
        os.makedirs(checkpoint_dir)
    saver.save(sess, os.path.join(checkpoint_dir, 'model.ckpt'))
    sess.close()


if __name__ == '__main__':
    tf.app.run()
