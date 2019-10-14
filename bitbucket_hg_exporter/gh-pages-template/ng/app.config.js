'use strict';

angular.
  module('BitbucketBackupApp').
  config(['$routeProvider',
    function config($routeProvider) {
      $routeProvider.
        when('/', {
          template: '<repo-list></repo-list>'
        }).
        when('/:project', {
          template: '<index-page></index-page>'
        }).
        when('/:project/issues', {
          redirectTo: '/:project/issues/page/1'
        }).
        when('/:project/issues/page/:pageId', {
          template: '<issues-list></issues-list>'
        }).
        when('/:project/issue/:issueId/page/:pageId?', {
          template: '<issue-details></issue-details>',
          reloadOnSearch: false,
        }).
        when('/:project/issue/:issueId', {
          redirectTo: '/:project/issue/:issueId/page/1'
        }).
        otherwise('/');
    }
  ]);