'use strict';

// Register `indexPage` component, along with its associated controller and template
angular.
  module('repoList').
  component('repoList', {
    templateUrl: 'ng/repo-list/repo-list.template.html',
    controller: ['$http', '$routeParams', '$scope', '$rootScope', function IndexPageController($http, $routeParams, $scope, $rootScope) {
        var self = this;
        self.scope = $scope;
        
    }]
  });