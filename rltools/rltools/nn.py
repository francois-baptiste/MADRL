import hashlib
import json

import h5py
import numpy as np
import tensorflow as tf

from rltools import util


class Model(object):

    def get_variables(self):
        """Get all variables in the graph"""
        return tf.get_collection(tf.GraphKeys.VARIABLES, self.varscope.name)

    def get_trainable_variables(self):
        """Get trainable variables in the graph"""
        assert self.varscope
        return tf.get_collection(tf.GraphKeys.TRAINABLE_VARIABLES, self.varscope.name)

    def get_num_params(self):
        return sum(v.get_shape().num_elements() for v in self.get_trainable_variables())

    @staticmethod
    def _hash_name2array(name2array):

        def hash_array(a):
            return '%.10f,%.10f,%d' % (np.mean(a), np.var(a), np.argmax(a))
        return hashlib.sha1(('|'.join('%s %s'
                                     for n, h in sorted([(name, hash_array(a)) for name, a in
                                                         name2array]))).encode('utf-8')).hexdigest()

    def savehash(self, sess):
        """Hash is based on values of variables"""
        vars_ = self.get_variables()
        vals = sess.run(vars_)
        return self._hash_name2array([(v.name, val) for v, val in util.safezip(vars_, vals)])

    # HDF5 saving and loading
    # The hierarchy in the HDF5 file reflects the hierarchy in the Tensorflow graph.
    def save_h5(self, sess, h5file, key, extra_attrs=None):
        with h5py.File(h5file, 'a') as f:
            if key in f:
                util.warn('WARNING: key {} already exists in {}'.format(key, h5file))
                dset = f[key]
            else:
                dset = f.create_group(key)

            vs = self.get_variables()
            vals = sess.run(vs)

            for v, val in util.safezip(vs, vals):
                dset[v.name] = val

            dset.attrs['hash'] = self.savehash(sess)
            if extra_attrs is not None:
                for k, v in extra_attrs:
                    if k in dset.attrs:
                        util.warn('Warning: attribute {} already exists in {}'.format(k, dset.name))
                    dset.attrs[k] = v

    def load_h5(self, sess, h5file, key):
        with h5py.File(h5file, 'r') as f:
            dset = f[key]

            ops = []
            for v in self.get_variables():
                util.header('Reading {}'.format(v.name))
                if v.name in dset:
                    ops.append(v.assign(dset[v.name][...]))
                else:
                    raise RuntimeError('Variable {} not found in {}'.format(v.name, dset))

            sess.run(ops)

            h = self.savehash(sess)
            assert h == dset[self.varscope.name].attrs[
                'hash'], 'Checkpoint hash {} does not match loaded hash {}'.format(
                    dset[self.varscope.name].attrs['hash'], h)

# Layers for feedforward networks


class Layer(Model):

    @property
    def output(self):
        raise NotImplementedError

    @property
    def output_shape(self):
        """Shape refers to the shape without the batch axis, which always implicitly goes first"""
        raise NotImplementedError


class ReshapeLayer(Layer):

    def __init__(self, input_, new_shape):
        self._output_shape = tuple(new_shape)
        util.header('Reshape(new_shape=%s)' % (str(self._output_shape),))
        with tf.variable_scope(type(self).__name__) as self.varscope:
            self._output = tf.reshape(input_, (-1,) + self._output_shape)

    @property
    def output(self):
        return self._output

    @property
    def output_shape(self):
        return self._output_shape


class AffineLayer(Layer):

    def __init__(self, input_B_Di, input_shape, output_shape, initializer):
        assert len(input_shape) == len(output_shape) == 1
        util.header('Affine(in=%d, out=%d)' % (input_shape[0], output_shape[0]))
        self._output_shape = (output_shape[0],)
        with tf.variable_scope(type(self).__name__) as self.varscope:
            if initializer is None:
                # initializer = tf.truncated_normal_initializer(mean=0., stddev=np.sqrt(2./input_shape[0]))
                initializer = tf.contrib.layers.xavier_initializer()
            self.W_Di_Do = tf.get_variable('W', shape=[input_shape[0], output_shape[0]],
                                           initializer=initializer)
            self.b_1_Do = tf.get_variable('b', shape=[1, output_shape[0]],
                                          initializer=tf.constant_initializer(0.))
            self.output_B_Do = tf.matmul(input_B_Di, self.W_Di_Do) + self.b_1_Do

    @property
    def output(self):
        return self.output_B_Do

    @property
    def output_shape(self):
        return self._output_shape


class NonlinearityLayer(Layer):

    def __init__(self, input_B_Di, output_shape, func):
        util.header('Nonlinearity(func=%s)' % func)
        self._output_shape = output_shape
        with tf.variable_scope(type(self).__name__) as self.varscope:
            self.output_B_Do = {'relu': tf.nn.relu,
                                'elu': tf.nn.elu,
                                'tanh': tf.tanh}[func](input_B_Di)

    @property
    def output(self):
        return self.output_B_Do

    @property
    def output_shape(self):
        return self._output_shape


class ConvLayer(Layer):

    def __init__(self, input_B_Ih_Iw_Ci, input_shape, Co, Fh, Fw, Oh, Ow, Sh, Sw, padding,
                 initializer):
        # TODO: calculate Oh and Ow from the other stuff.
        assert len(input_shape) == 3
        Ci = input_shape[2]
        util.header(
            'Conv(chanin=%d, chanout=%d, filth=%d, filtw=%d, outh=%d, outw=%d, strideh=%d, stridew=%d, padding=%s)'
            % (Ci, Co, Fh, Fw, Oh, Ow, Sh, Sw, padding))
        self._output_shape = (Oh, Ow, Co)
        with tf.variable_scope(type(self).__name__) as self.varscope:
            if initializer is None:
                # initializer = tf.truncated_normal_initializer(mean=0., stddev=np.sqrt(2./(Fh*Fw*Ci)))
                initializer = tf.contrib.layers.xavier_initializer()
            self.W_Fh_Fw_Ci_Co = tf.get_variable('W', shape=[Fh, Fw, Ci, Co],
                                                 initializer=initializer)
            self.b_1_1_1_Co = tf.get_variable('b', shape=[1, 1, 1, Co],
                                              initializer=tf.constant_initializer(0.))
            self.output_B_Oh_Ow_Co = tf.nn.conv2d(input_B_Ih_Iw_Ci, self.W_Fh_Fw_Ci_Co,
                                                  [1, Sh, Sw, 1], padding) + self.b_1_1_1_Co

    @property
    def output(self):
        return self.output_B_Oh_Ow_Co

    @property
    def output_shape(self):
        return self._output_shape


    @property
    def output(self): return self.output_B_Oh_Ow_Co
    @property
    def output_shape(self): return self._output_shape

def _check_keys(d, keys, optional):
    s = set(d.keys())
    if not (s == set(keys) or s == set(keys + optional)):
        raise RuntimeError('Got keys %s, but expected keys %s with optional keys %s' %
                           (str(s, str(keys), str(optional))))


def _parse_initializer(layerspec):
    if 'initializer' not in layerspec:
        return None
    initspec = layerspec['initializer']
    raise NotImplementedError('Unknown layer initializer type %s' % initspec['type'])


class FeedforwardNet(Layer):

    def __init__(self, input_B_Di, input_shape, layerspec_json):
        """
        Args:
            layerspec (string): JSON string describing layers
        """
        assert len(input_shape) >= 1
        self.input_B_Di = input_B_Di

        layerspec = json.loads(layerspec_json)
        util.ok('Loading feedforward net specification')
        util.header(json.dumps(layerspec, indent=2, separators=(',', ': ')))

        self.layers = []
        with tf.variable_scope(type(self).__name__) as self.varscope:

            prev_output, prev_output_shape = input_B_Di, input_shape

            for i_layer, ls in enumerate(layerspec):
                with tf.variable_scope('layer_%d' % i_layer):
                    if ls['type'] == 'reshape':
                        _check_keys(ls, ['type', 'new_shape'], [])
                        self.layers.append(ReshapeLayer(prev_output, ls['new_shape']))

                    elif ls['type'] == 'fc':
                        _check_keys(ls, ['type', 'n'], ['initializer'])
                        self.layers.append(AffineLayer(prev_output, prev_output_shape,
                                                       output_shape=(ls['n'],),
                                                       initializer=_parse_initializer(ls)))

                    elif ls['type'] == 'conv':
                        _check_keys(ls,
                                    ['type', 'chanout', 'filtsize', 'outsize', 'stride', 'padding'],
                                    ['initializer'])
                        self.layers.append(ConvLayer(
                            input_B_Ih_Iw_Ci=prev_output, input_shape=prev_output_shape, Co=ls[
                                'chanout'], Fh=ls['filtsize'], Fw=ls['filtsize'], Oh=ls['outsize'],
                            Ow=ls['outsize'], Sh=ls['stride'], Sw=ls['stride'], padding=ls[
                                'padding'], initializer=_parse_initializer(ls)))

                    elif ls['type'] == 'nonlin':
                        _check_keys(ls, ['type', 'func'], [])
                        self.layers.append(NonlinearityLayer(prev_output, prev_output_shape, ls[
                            'func']))

                    else:
                        raise NotImplementedError('Unknown layer type %s' % ls['type'])

                prev_output, prev_output_shape = self.layers[-1].output, self.layers[
                    -1].output_shape
                self._output, self._output_shape = prev_output, prev_output_shape

    @property
    def output(self):
        return self._output

    @property
    def output_shape(self):
        return self._output_shape


class NoOpStandardizer(object):

    def __init__(self, dim, eps=1e-6):
        pass

    def update(self, points_N_D):
        pass

    def standardize_expr(self, x_B_D):
        return x_B_D

    def unstandardize_expr(self, y_B_D):
        return y_B_D

    def standardize(self, x_B_D):
        return x_B_D

    def unstandardize(self, y_B_D):
        return y_B_D


class Standardizer(Model):

    def __init__(self, dim, eps=1e-6, init_count=0, init_mean=0., init_meansq=1.):
        """
        Args:
            dim: dimension of the space of points to be standardized
            eps: small constant to add to denominators to prevent division by 0
            init_count, init_mean, init_meansq: initial values for accumulators
        Note:
            if init_count is 0, then init_mean and init_meansq have no effect beyond
            the first call to update(), which will ignore their values and
            replace them with values from a new batch of data.
        """
        self._eps = eps
        self._dim = dim
        with tf.variable_scope(type(self).__name__) as self.varscope:
            self._count = tf.get_variable('count', shape=(
                1,), initializer=tf.constant_initializer(init_count), trainable=False)
            self._mean_1_D = tf.get_variable('mean_1_D', shape=(
                1, self._dim), initializer=tf.constant_initializer(init_mean), trainable=False)
            self._meansq_1_D = tf.get_variable('meansq_1_D', shape=(
                1, self._dim), initializer=tf.constant_initializer(init_meansq), trainable=False)
            self._stdev_1_D = tf.sqrt(self._meansq_1_D - tf.square(self._mean_1_D) + self._eps)

    def get_mean(self, sess):
        return sess.run(self._mean_1_D)

    def get_meansq(self, sess):
        return sess.run(self._meansq_1_D)

    def get_stdev(self, sess):
        # TODO: return with shape (1,D)
        return sess.run(self._stdev_1_D)

    def get_count(self, sess):
        return sess.run(self._count)

    def update(self, sess, points_N_D):
        assert points_N_D.ndim == 2 and points_N_D.shape[1] == self._dim
        num = points_N_D.shape[0]
        count = self.get_count(sess)
        a = count / (count + num)
        mean_op = self._mean_1_D.assign(a * self.get_mean(sess) + (1. - a) * points_N_D.mean(
            axis=0, keepdims=True))
        meansq_op = self._meansq_1_D.assign(a * self.get_meansq(sess) + (1. - a) * (
            points_N_D**2).mean(axis=0, keepdims=True))
        count_op = self._count.assign(count + num)
        sess.run([mean_op, meansq_op, count_op])

    def standardize_expr(self, x_B_D):
        return (x_B_D - self._mean_1_D) / (self._stdev_1_D + self._eps)

    def unstandardize_expr(self, y_B_D):
        return y_B_D * (self._stdev_1_D + self._eps) + self._mean_1_D

    def standardize(self, sess, x_B_D, centered=True):
        assert x_B_D.ndim == 2
        mu = 0.
        if centered:
            mu = self.get_mean(sess)
        return (x_B_D - mu) / (self.get_stdev(sess) + self._eps)

    def unstandardize(self, sess, y_B_D, centered=True):
        assert y_B_D.ndim == 2
        mu = 0.
        if centered:
            mu = self.get_mean(sess)
        return y_B_D * (self.get_stdev(sess) + self._eps) + mu