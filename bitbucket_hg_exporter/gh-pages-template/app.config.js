'use strict';

angular.
  module('BitbucketBackupApp').
  config(['$routeProvider',
    function config($routeProvider) {
      $routeProvider.
        when('/', {
          template: '<index-page></index-page>'
        }).
        when('/issues', {
          redirectTo: '/issues/page/1'
        }).
        when('/issues/page/:pageId', {
          template: '<issues-list></issues-list>'
        }).
        when('/issue/:issueId/page/:pageId?', {
          template: '<issue-details></issue-details>'
        }).
        when('/issue/:issueId', {
          redirectTo: '/issue/:issueId/page/1'
        }).
        otherwise('/');
    }
  ]);