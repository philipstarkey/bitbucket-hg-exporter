'use strict';

angular.
  module('BitbucketBackupApp').
  config(['$routeProvider',
    function config($routeProvider) {
      var wait_for_global_data = {
        init: ['InitService', function(Init) {
          return Init.promise;
        }]
      }

      $routeProvider.
        when('/', {
          template: '<repo-list></repo-list>',
          resolve: wait_for_global_data,
        }).
        when('/:owner/:project', {
          template: '<index-page></index-page>',
          resolve: wait_for_global_data,
        }).
        // Issues list
        when('/:owner/:project/issues', {
          redirectTo: '/:owner/:project/issues/page/1'
        }).
        when('/:owner/:project/issues/page/:pageId', {
          template: '<issues-list></issues-list>'
        }).
        // Issue details
        when('/:owner/:project/issues/:issueId/page/:pageId', {
          template: '<issue-details></issue-details>',
          reloadOnSearch: false,
          resolve: wait_for_global_data,
        }).
        when('/:owner/:project/issues/:issueId', {
          redirectTo: '/:owner/:project/issues/:issueId/page/1'
        }).
        when('/:owner/:project/issues/:issueId/:slug', {
          redirectTo: '/:owner/:project/issues/:issueId/page/1'
        }).
        when('/:owner/:project/issue/:issueId', {
          redirectTo: '/:owner/:project/issues/:issueId/page/1'
        }).
        when('/:owner/:project/issue/:issueId/page/:pageId', {
          redirectTo: '/:owner/:project/issues/:issueId/page/:pageId'
        }).
        when('/:owner/:project/issue/:issueId/:slug', {
          redirectTo: '/:owner/:project/issues/:issueId/page/1'
        }).
        // pull requests list
        when('/:owner/:project/pull-requests/page/:pageId', {
          template: '<pullrequests-list></pullrequests-list>',
          reloadOnSearch: false,
          resolve: wait_for_global_data,
        }).
        when('/:owner/:project/pull-requests', {
          redirectTo: '/:owner/:project/pull-requests/page/1'
        }).
        // pull requests details
        when('/:owner/:project/pull-requests/:prId/page/:pageId', {
          template: '<pullrequest-details></pullrequest-details>',
          reloadOnSearch: false,
          resolve: wait_for_global_data,
        }).
        when('/:owner/:project/pull-requests/:prId', {
          redirectTo: '/:owner/:project/pull-requests/:prId/page/1'
        }).
        when('/:owner/:project/pull-requests/:prId/:slug', {
          redirectTo: '/:owner/:project/pull-requests/:prId/page/1'
        }).
        // commit details
        when('/:owner/:project/commits/:commitSlug/page/:pageId', {
          template: '<commit-details></commit-details>',
          reloadOnSearch: false,
          resolve: wait_for_global_data,
        }).
        when('/:owner/:project/commits/:commitSlug', {
          redirectTo: '/:owner/:project/commits/:commitSlug/page/1'
        }).
        otherwise('/');
    }
  ]);