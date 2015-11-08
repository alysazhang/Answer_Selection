import cPickle
import gzip
import os
import sys
sys.setrecursionlimit(6000)
import time

import numpy
import theano
import theano.tensor as T
import theano.sandbox.neighbours as TSN
import time

from logistic_sgd import LogisticRegression
from mlp import HiddenLayer
from WPDefined import ConvFoldPoolLayer, dropout_from_layer, shared_dataset, repeat_whole_matrix
from cis.deep.utils.theano import debug_print
from theano.tensor.signal import downsample
from theano.tensor.nnet import conv
from loadData import load_wikiQA_corpus, load_word2vec_to_init, load_mts_wikiQA, load_wmf_wikiQA
from word2embeddings.nn.util import zero_value, random_value_normal
from common_functions import Conv_with_input_para, Average_Pooling_for_Top, create_conv_para
from random import shuffle

from sklearn import svm
from sklearn.multiclass import OneVsRestClassifier
from sklearn.svm import LinearSVC
from sklearn.linear_model import LinearRegression

from scipy import linalg, mat, dot

from preprocess_wikiQA import compute_map_mrr

#need to change
'''


4) fine-tune word embeddings
5) translation bettween
6) max sentence length to 40:   good
7) implement attention by euclid, not cosine: good
8) stop words by Yi Yang
9) normalized first word matching feature
10) only use bleu1 and nist1



Doesnt work:
1) lr0.08, kern30, window=5, update10
8) kern as Yu's paper
7) shuffle training data: should influence little as batch size is 1
3) use bleu and nist scores
1) true sentence lengths
2) unnormalized sentence length
8) euclid uses 1/exp(x)
'''

def evaluate_lenet5(learning_rate=0.05, n_epochs=2000, nkerns=[50], batch_size=1, window_width=3,
                    maxSentLength=64, emb_size=300, hidden_size=200,
                    margin=0.5, L2_weight=0.0001, update_freq=1, norm_threshold=5.0, max_truncate=60):
    maxSentLength=max_truncate+2*(window_width-1)
    model_options = locals().copy()
    print "model options", model_options
    rootPath='/mounts/data/proj/wenpeng/Dataset/WikiQACorpus/';
    rng = numpy.random.RandomState(23455)
    datasets, vocab_size=load_wikiQA_corpus(rootPath+'vocab.txt', rootPath+'WikiQA-train.txt', rootPath+'test_filtered.txt', max_truncate,maxSentLength)#vocab_size contain train, dev and test
    #datasets, vocab_size=load_wikiQA_corpus(rootPath+'vocab_lower_in_word2vec.txt', rootPath+'WikiQA-train.txt', rootPath+'test_filtered.txt', maxSentLength)#vocab_size contain train, dev and test
    mtPath='/mounts/data/proj/wenpeng/Dataset/WikiQACorpus/MT/BLEU_NIST/'
    mt_train, mt_test=load_mts_wikiQA(mtPath+'result_train/concate_2mt_train.txt', mtPath+'result_test/concate_2mt_test.txt')
    wm_train, wm_test=load_wmf_wikiQA(rootPath+'train_word_matching_scores.txt', rootPath+'test_word_matching_scores.txt')
    #wm_train, wm_test=load_wmf_wikiQA(rootPath+'train_word_matching_scores_normalized.txt', rootPath+'test_word_matching_scores_normalized.txt')
    indices_train, trainY, trainLengths, normalized_train_length, trainLeftPad, trainRightPad= datasets[0]
    indices_train_l=indices_train[::2,:]
    indices_train_r=indices_train[1::2,:]
    trainLengths_l=trainLengths[::2]
    trainLengths_r=trainLengths[1::2]
    normalized_train_length_l=normalized_train_length[::2]
    normalized_train_length_r=normalized_train_length[1::2]

    trainLeftPad_l=trainLeftPad[::2]
    trainLeftPad_r=trainLeftPad[1::2]
    trainRightPad_l=trainRightPad[::2]
    trainRightPad_r=trainRightPad[1::2]    
    indices_test, testY, testLengths,normalized_test_length, testLeftPad, testRightPad= datasets[1]
    indices_test_l=indices_test[::2,:]
    indices_test_r=indices_test[1::2,:]
    testLengths_l=testLengths[::2]
    testLengths_r=testLengths[1::2]
    normalized_test_length_l=normalized_test_length[::2]
    normalized_test_length_r=normalized_test_length[1::2]
    
    testLeftPad_l=testLeftPad[::2]
    testLeftPad_r=testLeftPad[1::2]
    testRightPad_l=testRightPad[::2]
    testRightPad_r=testRightPad[1::2]  

    n_train_batches=indices_train_l.shape[0]/batch_size
    n_test_batches=indices_test_l.shape[0]/batch_size
    
    train_batch_start=list(numpy.arange(n_train_batches)*batch_size)
    test_batch_start=list(numpy.arange(n_test_batches)*batch_size)

    
    indices_train_l=theano.shared(numpy.asarray(indices_train_l, dtype=theano.config.floatX), borrow=True)
    indices_train_r=theano.shared(numpy.asarray(indices_train_r, dtype=theano.config.floatX), borrow=True)
    indices_test_l=theano.shared(numpy.asarray(indices_test_l, dtype=theano.config.floatX), borrow=True)
    indices_test_r=theano.shared(numpy.asarray(indices_test_r, dtype=theano.config.floatX), borrow=True)
    indices_train_l=T.cast(indices_train_l, 'int64')
    indices_train_r=T.cast(indices_train_r, 'int64')
    indices_test_l=T.cast(indices_test_l, 'int64')
    indices_test_r=T.cast(indices_test_r, 'int64')
    


    rand_values=random_value_normal((vocab_size+1, emb_size), theano.config.floatX, numpy.random.RandomState(1234))
    rand_values[0]=numpy.array(numpy.zeros(emb_size),dtype=theano.config.floatX)
    #rand_values[0]=numpy.array([1e-50]*emb_size)
    rand_values=load_word2vec_to_init(rand_values, rootPath+'vocab_embs_300d.txt')
    #rand_values=load_word2vec_to_init(rand_values, rootPath+'vocab_lower_in_word2vec_embs_300d.txt')
    embeddings=theano.shared(value=rand_values, borrow=True)      
    
    cost_tmp=0
    error_sum=0
    
    # allocate symbolic variables for the data
    index = T.lscalar()
    x_index_l = T.lmatrix('x_index_l')   # now, x is the index matrix, must be integer
    x_index_r = T.lmatrix('x_index_r')
    y = T.lvector('y')  
    left_l=T.lscalar()
    right_l=T.lscalar()
    left_r=T.lscalar()
    right_r=T.lscalar()
    length_l=T.lscalar()
    length_r=T.lscalar()
    norm_length_l=T.dscalar()
    norm_length_r=T.dscalar()
    mts=T.dmatrix()
    wmf=T.dmatrix()
    #x=embeddings[x_index.flatten()].reshape(((batch_size*4),maxSentLength, emb_size)).transpose(0, 2, 1).flatten()
    ishape = (emb_size, maxSentLength)  # this is the size of MNIST images
    filter_size=(emb_size,window_width)
    #poolsize1=(1, ishape[1]-filter_size[1]+1) #?????????????????????????????
    length_after_wideConv=ishape[1]+filter_size[1]-1
    
    ######################
    # BUILD ACTUAL MODEL #
    ######################
    print '... building the model'

    # Reshape matrix of rasterized images of shape (batch_size,28*28)
    # to a 4D tensor, compatible with our LeNetConvPoolLayer
    #layer0_input = x.reshape(((batch_size*4), 1, ishape[0], ishape[1]))
    layer0_l_input = embeddings[x_index_l.flatten()].reshape((batch_size,maxSentLength, emb_size)).transpose(0, 2, 1).dimshuffle(0, 'x', 1, 2)
    layer0_r_input = embeddings[x_index_r.flatten()].reshape((batch_size,maxSentLength, emb_size)).transpose(0, 2, 1).dimshuffle(0, 'x', 1, 2)
    
    
    conv_W, conv_b=create_conv_para(rng, filter_shape=(nkerns[0], 1, filter_size[0], filter_size[1]))

    #layer0_output = debug_print(layer0.output, 'layer0.output')
    layer0_l = Conv_with_input_para(rng, input=layer0_l_input,
            image_shape=(batch_size, 1, ishape[0], ishape[1]),
            filter_shape=(nkerns[0], 1, filter_size[0], filter_size[1]), W=conv_W, b=conv_b)
    layer0_r = Conv_with_input_para(rng, input=layer0_r_input,
            image_shape=(batch_size, 1, ishape[0], ishape[1]),
            filter_shape=(nkerns[0], 1, filter_size[0], filter_size[1]), W=conv_W, b=conv_b)
    layer0_l_output=debug_print(layer0_l.output, 'layer0_l.output')
    layer0_r_output=debug_print(layer0_r.output, 'layer0_r.output')
    

    
    layer1=Average_Pooling_for_Top(rng, input_l=layer0_l_output, input_r=layer0_r_output, kern=nkerns[0],
                                       left_l=left_l, right_l=right_l, left_r=left_r, right_r=right_r, 
                                       length_l=length_l+filter_size[1]-1, length_r=length_r+filter_size[1]-1,
                                       dim=maxSentLength+filter_size[1]-1)
    

    
    
    #layer2=HiddenLayer(rng, input=layer1_out, n_in=nkerns[0]*2, n_out=hidden_size, activation=T.tanh)
    
    
    sum_uni_l=T.sum(layer0_l_input, axis=3).reshape((1, emb_size))
    aver_uni_l=sum_uni_l/layer0_l_input.shape[3]
    norm_uni_l=sum_uni_l/T.sqrt((sum_uni_l**2).sum())
    sum_uni_r=T.sum(layer0_r_input, axis=3).reshape((1, emb_size))
    aver_uni_r=sum_uni_r/layer0_r_input.shape[3]
    norm_uni_r=sum_uni_r/T.sqrt((sum_uni_r**2).sum())
    
    uni_cosine=cosine(sum_uni_l, sum_uni_r)
    aver_uni_cosine=cosine(aver_uni_l, aver_uni_r)
    uni_sigmoid_simi=debug_print(T.nnet.sigmoid(T.dot(norm_uni_l, norm_uni_r.T)).reshape((1,1)),'uni_sigmoid_simi')    
    '''
    linear=Linear(sum_uni_l, sum_uni_r)
    poly=Poly(sum_uni_l, sum_uni_r)
    sigmoid=Sigmoid(sum_uni_l, sum_uni_r)
    rbf=RBF(sum_uni_l, sum_uni_r)
    gesd=GESD(sum_uni_l, sum_uni_r)
    '''
    eucli_1=1.0/(1.0+EUCLID(sum_uni_l, sum_uni_r))#25.2%
    #eucli_1_exp=1.0/T.exp(EUCLID(sum_uni_l, sum_uni_r))
    
    len_l=norm_length_l.reshape((1,1))
    len_r=norm_length_r.reshape((1,1))  
    
    '''
    len_l=length_l.reshape((1,1))
    len_r=length_r.reshape((1,1))  
    '''
    #length_gap=T.log(1+(T.sqrt((len_l-len_r)**2))).reshape((1,1))
    #length_gap=T.sqrt((len_l-len_r)**2)
    #layer3_input=mts
    layer3_input=T.concatenate([#mts,
                                uni_cosine,#eucli_1_exp,#uni_sigmoid_simi,  #norm_uni_l-(norm_uni_l+norm_uni_r)/2,#uni_cosine, #
                                layer1.output_cosine,  #layer1.output_eucli_to_simi_exp,#layer1.output_sigmoid_simi,#layer1.output_vector_l-(layer1.output_vector_l+layer1.output_vector_r)/2,#layer1.output_cosine, #
                                len_l, len_r,wmf
                                ], axis=1)#, layer2.output, layer1.output_cosine], axis=1)
    #layer3_input=T.concatenate([mts,eucli, uni_cosine, len_l, len_r, norm_uni_l-(norm_uni_l+norm_uni_r)/2], axis=1)
    #layer3=LogisticRegression(rng, input=layer3_input, n_in=11, n_out=2)
    layer3=LogisticRegression(rng, input=layer3_input, n_in=(1)+(1)+2+2, n_out=2)
    
    #L2_reg =(layer3.W** 2).sum()+(layer2.W** 2).sum()+(layer1.W** 2).sum()+(conv_W** 2).sum()
    L2_reg =debug_print((layer3.W** 2).sum()+(conv_W** 2).sum(), 'L2_reg')#+(layer1.W** 2).sum()++(embeddings**2).sum()
    cost_this =debug_print(layer3.negative_log_likelihood(y), 'cost_this')#+L2_weight*L2_reg
    cost=debug_print((cost_this+cost_tmp)/update_freq+L2_weight*L2_reg, 'cost')
    

    
    test_model = theano.function([index], [layer3.prop_for_posi,layer3_input, y],
          givens={
            x_index_l: indices_test_l[index: index + batch_size],
            x_index_r: indices_test_r[index: index + batch_size],
            y: testY[index: index + batch_size],
            left_l: testLeftPad_l[index],
            right_l: testRightPad_l[index],
            left_r: testLeftPad_r[index],
            right_r: testRightPad_r[index],
            length_l: testLengths_l[index],
            length_r: testLengths_r[index],
            norm_length_l: normalized_test_length_l[index],
            norm_length_r: normalized_test_length_r[index],
            mts: mt_test[index: index + batch_size],
            wmf: wm_test[index: index + batch_size]}, on_unused_input='ignore')


    #params = layer3.params + layer2.params + layer1.params+ [conv_W, conv_b]
    params = layer3.params+ [conv_W, conv_b]#+[embeddings]# + layer1.params 
    
    accumulator=[]
    for para_i in params:
        eps_p=numpy.zeros_like(para_i.get_value(borrow=True),dtype=theano.config.floatX)
        accumulator.append(theano.shared(eps_p, borrow=True))
      
    # create a list of gradients for all model parameters
    grads = T.grad(cost, params)

    updates = []
    for param_i, grad_i, acc_i in zip(params, grads, accumulator):
        grad_i=debug_print(grad_i,'grad_i')
        acc = acc_i + T.sqr(grad_i)
        updates.append((param_i, param_i - learning_rate * grad_i / T.sqrt(acc)))   #AdaGrad
        updates.append((acc_i, acc))    
  
    train_model = theano.function([index], [cost,layer3.errors(y), layer3_input], updates=updates,
          givens={
            x_index_l: indices_train_l[index: index + batch_size],
            x_index_r: indices_train_r[index: index + batch_size],
            y: trainY[index: index + batch_size],
            left_l: trainLeftPad_l[index],
            right_l: trainRightPad_l[index],
            left_r: trainLeftPad_r[index],
            right_r: trainRightPad_r[index],
            length_l: trainLengths_l[index],
            length_r: trainLengths_r[index],
            norm_length_l: normalized_train_length_l[index],
            norm_length_r: normalized_train_length_r[index],
            mts: mt_train[index: index + batch_size],
            wmf: wm_train[index: index + batch_size]}, on_unused_input='ignore')

    train_model_predict = theano.function([index], [cost_this,layer3.errors(y), layer3_input, y, layer1.output_vector_l, layer1.output_vector_r, layer1.output_cosine],
          givens={
            x_index_l: indices_train_l[index: index + batch_size],
            x_index_r: indices_train_r[index: index + batch_size],
            y: trainY[index: index + batch_size],
            left_l: trainLeftPad_l[index],
            right_l: trainRightPad_l[index],
            left_r: trainLeftPad_r[index],
            right_r: trainRightPad_r[index],
            length_l: trainLengths_l[index],
            length_r: trainLengths_r[index],
            norm_length_l: normalized_train_length_l[index],
            norm_length_r: normalized_train_length_r[index],
            mts: mt_train[index: index + batch_size],
            wmf: wm_train[index: index + batch_size]}, on_unused_input='ignore')



    ###############
    # TRAIN MODEL #
    ###############
    print '... training'
    # early-stopping parameters
    patience = 500000000000000  # look as this many examples regardless
    patience_increase = 2  # wait this much longer when a new best is
                           # found
    improvement_threshold = 0.995  # a relative improvement of this much is
                                   # considered significant
    validation_frequency = min(n_train_batches, patience / 2)
                                  # go through this many
                                  # minibatche before checking the network
                                  # on the validation set; in this case we
                                  # check every epoch

    best_params = None
    best_validation_loss = numpy.inf
    best_iter = 0
    test_score = 0.
    start_time = time.clock()

    epoch = 0
    done_looping = False
    
    svm_max=0.0
    best_epoch=0

    while (epoch < n_epochs) and (not done_looping):
        epoch = epoch + 1
        #for minibatch_index in xrange(n_train_batches): # each batch
        minibatch_index=0
        #shuffle(train_batch_start)#shuffle training data
        
        for batch_start in train_batch_start: 
            # iter means how many batches have been runed, taking into loop
            iter = (epoch - 1) * n_train_batches + minibatch_index +1

            minibatch_index=minibatch_index+1
            #if epoch %2 ==0:
            #    batch_start=batch_start+remain_train
            #time.sleep(0.5)
            #print batch_start
            if iter%update_freq != 0:
                cost_ij, error_ij, layer3_input, y, sum_uni_l, sum_uni_r, uni_cosine=train_model_predict(batch_start)
                #print 'layer3_input', layer3_input
                cost_tmp+=cost_ij
                error_sum+=error_ij
            else:
                cost_average, error_ij, layer3_input= train_model(batch_start)
                #print 'layer3_input', layer3_input
                error_sum=0
                cost_tmp=0#reset for the next batch
                #print layer3_input
                #exit(0)
            #exit(0)
            if iter % n_train_batches == 0:
                print 'training @ iter = '+str(iter)+' average cost: '+str(cost_average)+' error: '+str(error_sum)+'/'+str(update_freq)+' error rate: '+str(error_sum*1.0/update_freq)
            #if iter ==1:
            #    exit(0)
            
            if iter % validation_frequency == 0:
                #write_file=open('log.txt', 'w')
                test_probs=[]
                test_y=[]
                test_features=[]
                for i in test_batch_start:
                    prob_i, layer3_input, y=test_model(i)
                    #test_losses = [test_model(i) for i in test_batch_start]
                    test_probs.append(prob_i[0][0])
                    test_y.append(y[0])
                    test_features.append(layer3_input[0])

                MAP, MRR=compute_map_mrr(rootPath+'test_filtered.txt', test_probs)
                #now, check MAP and MRR
                print(('\t\t\t\t\t\tepoch %i, minibatch %i/%i, test MAP of best '
                           'model %f, MRR  %f') %
                          (epoch, minibatch_index, n_train_batches,MAP, MRR))
                #now, see the results of LR
                #write_feature=open(rootPath+'feature_check.txt', 'w')
                train_y=[]
                train_features=[]
                count=0
                for batch_start in train_batch_start: 
                    cost_ij, error_ij, layer3_input, y, sum_uni_l, sum_uni_r, uni_cosine=train_model_predict(batch_start)
                    train_y.append(y[0])
                    train_features.append(layer3_input[0])
                    #write_feature.write(str(batch_start)+' '+' '.join(map(str,layer3_input[0]))+'\n')
                    #count+=1

                #write_feature.close()

                clf = svm.SVC(C=1.0, kernel='linear')
                clf.fit(train_features, train_y)
                results_svm=clf.decision_function(test_features)
                MAP_svm, MRR_svm=compute_map_mrr(rootPath+'test_filtered.txt', results_svm)
                
                lr=LinearRegression().fit(train_features, train_y)
                results_lr=lr.predict(test_features)
                MAP_lr, MRR_lr=compute_map_mrr(rootPath+'test_filtered.txt', results_lr)
                print '\t\t\t\t\t\t\tSVM, MAP: ', MAP_svm, ' MRR: ', MRR_svm, ' LR: ', MAP_lr, ' MRR: ', MRR_lr

            if patience <= iter:
                done_looping = True
                break
        #after each epoch, increase the batch_size
        if epoch%2==1:
            update_freq=update_freq*1
        else:
            update_freq=update_freq/1
        #print 'Batch_size: ', update_freq
    end_time = time.clock()
    print('Optimization complete.')
    print('Best validation score of %f %% obtained at iteration %i,'\
          'with test performance %f %%' %
          (best_validation_loss * 100., best_iter + 1, test_score * 100.))
    print >> sys.stderr, ('The code for file ' +
                          os.path.split(__file__)[1] +
                          ' ran for %.2fm' % ((end_time - start_time) / 60.))


def cosine(vec1, vec2):
    vec1=debug_print(vec1, 'vec1')
    vec2=debug_print(vec2, 'vec2')
    norm_uni_l=T.sqrt((vec1**2).sum())
    norm_uni_r=T.sqrt((vec2**2).sum())
    
    dot=T.dot(vec1,vec2.T)
    
    simi=debug_print(dot/(norm_uni_l*norm_uni_r), 'uni-cosine')
    return simi.reshape((1,1))    
def Linear(sum_uni_l, sum_uni_r):
    return (T.dot(sum_uni_l,sum_uni_r.T)).reshape((1,1))    
def Poly(sum_uni_l, sum_uni_r):
    dot=T.dot(sum_uni_l,sum_uni_r.T)
    poly=(0.5*dot+1)**3
    return poly.reshape((1,1))    
def Sigmoid(sum_uni_l, sum_uni_r):
    dot=T.dot(sum_uni_l,sum_uni_r.T)
    return T.tanh(1.0*dot+1).reshape((1,1))    
def RBF(sum_uni_l, sum_uni_r):
    eucli=T.sum((sum_uni_l-sum_uni_r)**2)
    return T.exp(-0.5*eucli).reshape((1,1))    
def GESD (sum_uni_l, sum_uni_r):
    eucli=1/(1+T.sum((sum_uni_l-sum_uni_r)**2))
    kernel=1/(1+T.exp(-(T.dot(sum_uni_l,sum_uni_r.T)+1)))
    return (eucli*kernel).reshape((1,1))   
def EUCLID(sum_uni_l, sum_uni_r):
    return T.sqrt(T.sqr(sum_uni_l-sum_uni_r).sum()+1e-20).reshape((1,1))
    


if __name__ == '__main__':
    evaluate_lenet5()