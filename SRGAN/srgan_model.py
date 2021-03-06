import tensorflow as tf


tf.set_random_seed(777)  # reproducibility


def sub_pixel_conv2d(x, f, s=2):
    """
    # ref : https://github.com/tensorlayer/SRGAN/blob/master/tensorlayer/layers.py
    """
    if f is None:
        f = int(int(x.get_shape()[-1]) / (s ** 2))

    bsize, a, b, c = x.get_shape().as_list()
    bsize = tf.shape(x)[0]

    x_s = tf.split(x, s, 3)
    x_r = tf.concat(x_s, 2)

    return tf.reshape(x_r, (bsize, s * a, s * b, f))


def conv2d(x, f=64, k=3, d=1, act=None, pad='SAME', name='conv2d'):
    """
    :param x: input
    :param f: filters, default 64
    :param k: kernel size, default 3
    :param d: strides, default 2
    :param act: activation function, default None
    :param pad: padding (valid or same), default same
    :param name: scope name, default conv2d
    :return: covn2d net
    """
    return tf.layers.conv2d(x,
                            filters=f, kernel_size=k, strides=d,
                            kernel_initializer=tf.contrib.layers.variance_scaling_initializer(),
                            kernel_regularizer=tf.contrib.layers.l2_regularizer(5e-4),
                            bias_initializer=tf.zeros_initializer(),
                            activation=act,
                            padding=pad, name=name)


def batch_norm(x, momentum=0.9, eps=1e-9):
    return tf.layers.batch_normalization(inputs=x,
                                         momentum=momentum,
                                         epsilon=eps,
                                         scale=True,
                                         training=True)


class SRGAN:

    def __init__(self, s, batch_size=16, input_height=28, input_width=28, input_channel=1,
                 sample_num=28 * 28, sample_size=28, output_height=28, output_width=28,
                 df_dim=64, gf_dim=64,
                 g_lr=1e-4, d_lr=1e-4):

        """
        # General Settings
        :param s: TF Session
        :param batch_size: training batch size, default 16
        :param input_height: input image height, default 28
        :param input_width: input image width, default 28
        :param input_channel: input image channel, default 1 (RGB)
        - in case of MNIST, image size is 28x28x1(HWC).

        # Output Settings
        :param sample_num: the number of output images, default 784
        :param sample_size: sample image size, default 28
        :param output_height: output images height, default 28
        :param output_width: output images width, default 28

        # For CNN model
        :param df_dim: discriminator filter, default 64
        :param gf_dim: generator filter, default 64

        # Training Option
        :param g_lr: generator learning rate, default 1e-4
        :param d_lr: discriminator learning rate, default 1e-4
        """

        self.s = s
        self.batch_size = batch_size

        self.input_height = input_height
        self.input_width = input_width
        self.input_channel = input_channel
        self.lr_image_shape = [None, self.input_height // 2, self.input_width // 2, self.input_channel]
        self.hr_image_shape = [None, self.input_height, self.input_width, self.input_channel]

        self.sample_num = sample_num
        self.sample_size = sample_size
        self.output_height = output_height
        self.output_width = output_width

        self.df_dim = df_dim
        self.gf_dim = gf_dim

        self.beta1 = 0.9
        self.beta2 = 0.999
        self.d_lr = d_lr
        self.g_lr = g_lr
        self.lr_decay_rate = 0.5
        self.lr_low_boundary = 1e-5

        # LR update
        # self.d_lr_update = tf.assign(self.d_lr, tf.maximum(self.d_lr * self.lr_decay_rate, self.lr_low_boundary))
        # self.g_lr_update = tf.assign(self.g_lr, tf.maximum(self.g_lr * self.lr_decay_rate, self.lr_low_boundary))

        # pre-defined
        self.d_real = 0
        self.d_fake = 0
        self.d_loss = 0.
        self.g_loss = 0.

        self.d_op = None
        self.g_op = None
        self.merged = None
        self.writer = None
        self.saver = None

        # Placeholders
        self.x_lr = tf.placeholder(tf.float32, shape=self.lr_image_shape, name="x-image-lr")  # (-1, 14, 14, 1)
        self.x_hr = tf.placeholder(tf.float32, shape=self.hr_image_shape, name="x-image-hr")  # (-1, 28, 28, 1)

        self.build_srgan()  # build SRGAN model

    def discriminator(self, x, reuse=None):
        """
        # Following a network architecture referred in the paper
        # For MNIST
        :param x: images (-1, 28, 28, 1)
        :param reuse: re-usability
        :return: prob
        """
        with tf.variable_scope("discriminator", reuse=reuse):
            x = conv2d(x, self.df_dim, act=tf.nn.leaky_relu, name='d-conv2d-0')

            strides = [1, 2]        # [2, 1]
            filters = [2, 4, 4, 8]  # [2, 4, 4, 4, 4, 8, 8]
            filters = list(map(lambda fs: fs * self.df_dim, filters))

            for i, f in enumerate(filters):
                x = conv2d(x, f=f, d=strides[i % 2], name='d-conv2d-%d' % (i + 1))
                x = batch_norm(x)
                x = tf.nn.leaky_relu(x)

            # x : (-1, 7, 7, 8 * 64)
            x = tf.layers.flatten(x)  # (-1, 7 * 7 * 8 * 64)

            x = tf.layers.dense(x, 1024, activation=tf.nn.leaky_relu, name='d-fc-0')
            x = tf.layers.dense(x, 1, activation=tf.nn.sigmoid, name='d-fc-1')

            return x

    def generator(self, x, reuse=None):
        """
        # For MNIST
        :param x: LR (Low Resolution) images, (-1, 14, 14, 1)
        :param reuse: re-usability
        :return: prob
        """
        with tf.variable_scope("generator", reuse=reuse):
            def residual_block(x, name):
                with tf.variable_scope(name):
                    x = conv2d(x, self.gf_dim, name=name)
                    x = batch_norm(x)
                    x = tf.nn.relu(x)

                    return x

            x = conv2d(x, self.gf_dim, act=tf.nn.relu, name='g-conv2d-0')
            x_ = x  # for later, used at layer concat

            # B residual blocks
            for i in range(1, 9):  # (1, 17)
                xx = residual_block(x, name='g-residual_block_%d' % (i * 2 - 1))
                xx = residual_block(xx, name='g-residual_block_%d' % (i * 2))
                xx = tf.add(x, xx)
                x = xx

            x = conv2d(x, self.gf_dim, name='g-conv2d-1')
            x = batch_norm(x)

            x = tf.add(x_, x)

            for i in range(1, 2):
                x = conv2d(x, self.gf_dim * 4, name='g-conv2d-%d' % (i + 2))
                x = sub_pixel_conv2d(x, f=None, s=2)
                x = tf.nn.relu(x)

            x = conv2d(x, self.input_channel, k=1, name='g-conv2d-5')  # (-1, 28, 28, 1)
            x = tf.nn.sigmoid(x)

            return x

    def build_srgan(self):
        def mse_loss(pred, data, n=self.batch_size):
            return tf.sqrt(2 * tf.nn.l2_loss(pred - data)) / n

        # Generator
        self.g = self.generator(self.x_lr)

        # Discriminator
        d_real = self.discriminator(self.x_hr)
        d_fake = self.discriminator(self.g, reuse=True)

        # Following LSGAN Loss
        d_real_loss = tf.reduce_sum(mse_loss(d_real, tf.ones_like(d_real)))
        d_fake_loss = tf.reduce_sum(mse_loss(d_fake, tf.zeros_like(d_fake)))
        self.d_loss = (d_real_loss + d_fake_loss) / 2.
        self.g_loss = tf.reduce_sum(mse_loss(d_fake, tf.ones_like(d_fake)))

        # Summary
        tf.summary.image("g", self.g)  # generated images by Generative Model
        tf.summary.scalar("d_real_loss", d_real_loss)
        tf.summary.scalar("d_fake_loss", d_fake_loss)
        tf.summary.scalar("d_loss", self.d_loss)
        tf.summary.scalar("g_loss", self.g_loss)

        # Optimizer
        t_vars = tf.trainable_variables()
        d_params = [v for v in t_vars if v.name.startswith('d')]
        g_params = [v for v in t_vars if v.name.startswith('g')]

        self.d_op = tf.train.AdamOptimizer(learning_rate=self.d_lr,
                                           beta1=self.beta1, beta2=self.beta2).minimize(self.d_loss, var_list=d_params)
        self.g_op = tf.train.AdamOptimizer(learning_rate=self.g_lr,
                                           beta1=self.beta1, beta2=self.beta2).minimize(self.g_loss, var_list=g_params)

        # Merge summary
        self.merged = tf.summary.merge_all()

        # Model saver
        self.saver = tf.train.Saver(max_to_keep=1)
        self.writer = tf.summary.FileWriter('./model/', self.s.graph)
