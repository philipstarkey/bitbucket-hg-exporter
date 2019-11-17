'use strict';

angular.
  module('BitbucketBackupApp').
  config(['$routeProvider',
    function config($routeProvider) {
      $routeProvider.
        when('/', {
          template: '<repo-list></repo-list>'
        }).
        when('/:owner/:project', {
          template: '<index-page></index-page>'
        }).
        when('/:owner/:project/issues', {
          redirectTo: '/:owner/:project/issues/page/1'
        }).
        when('/:owner/:project/issues/page/:pageId', {
          template: '<issues-list></issues-list>'
        }).
        when('/:owner/:project/issue/:issueId/page/:pageId?', {
          template: '<issue-details></issue-details>',
          reloadOnSearch: false,
        }).
        when('/:owner/:project/issue/:issueId', {
          redirectTo: '/:owner/:project/issue/:issueId/page/1'
        }).
        when('/:owner/:project/pull-requests/:prId/page/:pageId?', {
          template: '<issue-details></issue-details>',
          reloadOnSearch: false,
        }).
        when('/:owner/:project/pull-requests/:prId', {
          redirectTo: '/:owner/:project/issue/:prId/page/1'
        }).
        otherwise('/');
    }
  ]);